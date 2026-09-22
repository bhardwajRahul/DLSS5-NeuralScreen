// gpu_recorder_check.exe - records a few seconds of a synthetic picture and
// a sine tone through gpu_recorder.cpp, with no worker and no NGX: the
// encoder chain, the colour conversion, the audio ring and the mid-recording
// resize, checked on this machine's GPU. tests/test_gpu_recorder.py builds
// it, runs it and checks the file it writes.
//
//   gpu_recorder_check <out.mp4> [seconds] [width] [height] [codec] [fps] [resize] [sync] [pace]
//
// codec: 0 auto, 1 H.264, 2 HEVC, 3 AV1. resize 1: halfway through, the
// source becomes 4:3 at two thirds of the size (it must come out letterboxed).
// sync 1: the tone is silent except for a 50 ms burst at every whole second
// from 0.5 s on, and the frames of those instants are white - the offset
// between the two in the file is the recording's A/V sync error.
// pace N: frames come at an uneven ~N fps instead of one per slot - a live
// pipeline slower than the recording's clock, where most slots are empty and
// the file is variable-rate. The sync marks must stay put over time then too.
// The picture: the top half is four bars - red, green, blue, grey 128 - and
// the bottom half is black with a white square that moves one step a frame.
// Exit code 0 when the recording finished without an error.
#include <d3d12.h>
#include <dxgi1_4.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "gpu_recorder.h"

namespace {

void Fill(BYTE *dst, UINT pitch, UINT w, UINT h, uint32_t frame, bool flash)
{
    if (flash)
    {
        for (UINT y = 0; y < h; ++y)
            memset(dst + static_cast<size_t>(y) * pitch, 255, static_cast<size_t>(w) * 4);
        return;
    }
    const BYTE bars[4][3] = { {255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {128, 128, 128} };
    const UINT box = (std::max)(16u, h / 8);
    const UINT bx = (frame * 8) % (w - box);
    for (UINT y = 0; y < h; ++y)
    {
        BYTE *row = dst + static_cast<size_t>(y) * pitch;
        for (UINT x = 0; x < w; ++x)
        {
            BYTE *px = row + x * 4;
            if (y < h / 2)
            {
                const BYTE *c = bars[(x * 4) / w];
                px[0] = c[0]; px[1] = c[1]; px[2] = c[2];
            }
            else
            {
                const bool in = x >= bx && x < bx + box && y >= h * 3 / 4 - box / 2 &&
                                y < h * 3 / 4 + box / 2;
                px[0] = px[1] = px[2] = in ? 255 : 0;
            }
            px[3] = 255;
        }
    }
}

struct Source
{
    ID3D12Resource *tex = nullptr;
    ID3D12Resource *upload = nullptr;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT fp = {};
    UINT w = 0, h = 0;
};

bool MakeSource(ID3D12Device *dev, UINT w, UINT h, Source &s)
{
    s.w = w;
    s.h = h;
    D3D12_HEAP_PROPERTIES hp = { D3D12_HEAP_TYPE_DEFAULT };
    D3D12_RESOURCE_DESC td = {};
    td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width = w;
    td.Height = h;
    td.DepthOrArraySize = 1;
    td.MipLevels = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;   // the worker's output format
    td.SampleDesc.Count = 1;
    td.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;   // as v.output
    if (FAILED(dev->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                                            D3D12_RESOURCE_STATE_COPY_DEST, nullptr,
                                            IID_PPV_ARGS(&s.tex))))
        return false;
    UINT64 bytes = 0;
    dev->GetCopyableFootprints(&td, 0, 1, 0, &s.fp, nullptr, nullptr, &bytes);
    D3D12_HEAP_PROPERTIES up = { D3D12_HEAP_TYPE_UPLOAD };
    D3D12_RESOURCE_DESC bd = {};
    bd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    bd.Width = bytes;
    bd.Height = 1;
    bd.DepthOrArraySize = 1;
    bd.MipLevels = 1;
    bd.SampleDesc.Count = 1;
    bd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    return SUCCEEDED(dev->CreateCommittedResource(&up, D3D12_HEAP_FLAG_NONE, &bd,
                                                  D3D12_RESOURCE_STATE_GENERIC_READ,
                                                  nullptr, IID_PPV_ARGS(&s.upload)));
}

}  // namespace

int wmain(int argc, wchar_t **argv)
{
    if (argc < 2)
    {
        fprintf(stderr, "usage: gpu_recorder_check <out.mp4> [seconds] [width] [height] "
                        "[codec] [fps] [resize] [sync] [pace]\n");
        return 2;
    }
    const wchar_t *path = argv[1];
    const double seconds = argc > 2 ? _wtof(argv[2]) : 3.0;
    const UINT width = argc > 3 ? _wtoi(argv[3]) : 1920;
    const UINT height = argc > 4 ? _wtoi(argv[4]) : 1080;
    const uint32_t codec = argc > 5 ? _wtoi(argv[5]) : GPUREC_CODEC_AUTO;
    const UINT fps = argc > 6 ? _wtoi(argv[6]) : 60;
    const bool resize = argc > 7 && _wtoi(argv[7]) != 0;
    const bool sync = argc > 8 && _wtoi(argv[8]) != 0;
    const double pace = argc > 9 ? _wtof(argv[9]) : 0.0;
    // Uneven, like a real pipeline: the intervals cycle around 1/pace.
    const double jitter[5] = { 0.90, 1.08, 1.00, 1.20, 0.82 };
    double next_frame = 0.0;
    uint32_t paced = 0;
    // The instants of the sync marks, in seconds from the file's time 0.
    auto marked = [](double t) { return t >= 0.5 && std::fmod(t, 1.0) < 0.05; };

    // The adapter with the most video memory: the card the worker runs on.
    IDXGIFactory4 *factory = nullptr;
    if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) return 3;
    IDXGIAdapter1 *best = nullptr;
    SIZE_T best_mem = 0;
    IDXGIAdapter1 *a = nullptr;
    for (UINT i = 0; factory->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; ++i)
    {
        DXGI_ADAPTER_DESC1 d = {};
        a->GetDesc1(&d);
        if ((d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) == 0 && d.DedicatedVideoMemory > best_mem)
        {
            if (best) best->Release();
            best = a;
            best_mem = d.DedicatedVideoMemory;
            continue;
        }
        a->Release();
    }
    ID3D12Device *dev = nullptr;
    if (best == nullptr ||
        FAILED(D3D12CreateDevice(best, D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&dev))))
    {
        fprintf(stderr, "no D3D12 device\n");
        return 3;
    }
    DXGI_ADAPTER_DESC1 ad = {};
    best->GetDesc1(&ad);
    fwprintf(stderr, L"adapter: %s\n", ad.Description);

    ID3D12CommandQueue *queue = nullptr;
    D3D12_COMMAND_QUEUE_DESC qd = { D3D12_COMMAND_LIST_TYPE_DIRECT };
    dev->CreateCommandQueue(&qd, IID_PPV_ARGS(&queue));
    ID3D12CommandAllocator *alloc = nullptr;
    dev->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&alloc));
    ID3D12GraphicsCommandList *list = nullptr;
    dev->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, alloc, nullptr,
                           IID_PPV_ARGS(&list));
    list->Close();
    ID3D12Fence *fence = nullptr;
    dev->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence));
    HANDLE ev = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    UINT64 fv = 0;

    Source big, other;
    if (!MakeSource(dev, width, height, big)) { fprintf(stderr, "no source\n"); return 3; }
    if (resize && !MakeSource(dev, (height * 2 / 3) * 4 / 3, height * 2 / 3, other))
    { fprintf(stderr, "no second source\n"); return 3; }

    // The audio ring, as the client makes it: 2 s of 48 kHz stereo.
    const uint32_t rate = 48000, channels = 2, capacity = rate * 2;
    char ring_name[64];
    sprintf_s(ring_name, "Local\\ns_grec_check_%lu", GetCurrentProcessId());
    const DWORD ring_bytes = sizeof(GpuRecAudioRing) + capacity * channels * 2;
    HANDLE map = CreateFileMappingA(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                    ring_bytes, ring_name);
    auto *ring = static_cast<GpuRecAudioRing *>(MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, 0));
    ring->magic = GPUREC_AUDIO_MAGIC;
    ring->rate = rate;
    ring->channels = channels;
    ring->capacity = capacity;
    ring->written = 0;
    auto *pcm = reinterpret_cast<int16_t *>(ring + 1);

    LARGE_INTEGER freq, t0;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&t0);

    GpuRecParams p = {};
    p.path = path;
    p.width = width;
    p.height = height;
    p.fps = fps;
    p.codec = codec;
    p.start_qpc = t0.QuadPart;
    p.audio_name = ring_name;
    GpuRecStarted started = {};
    if (!GpuRecStart(dev, p, &started))
    {
        fprintf(stderr, "start failed: 0x%08X\n", static_cast<unsigned>(started.hr));
        return 4;
    }
    fprintf(stderr, "recording: codec %u, audio %d, setup %.0f ms\n", started.codec,
            started.audio ? 1 : 0,
            1000.0 * (started.origin_qpc - t0.QuadPart) / freq.QuadPart);

    // A 440 Hz tone, written in real time the way the client's capture does.
    std::atomic<bool> stop{false};
    std::thread tone([&] {
        int64_t n = 0;
        while (!stop.load())
        {
            LARGE_INTEGER now;
            QueryPerformanceCounter(&now);
            const int64_t due = (now.QuadPart - t0.QuadPart) * rate / freq.QuadPart;
            for (; n < due; ++n)
            {
                // Ring frame n was at t0 + n / rate; the file's time 0 is the origin.
                const double t_file = static_cast<double>(n) / rate -
                    static_cast<double>(started.origin_qpc - t0.QuadPart) / freq.QuadPart;
                const bool on = !sync || marked(t_file);
                const int16_t v = on ? static_cast<int16_t>(
                    8000.0 * std::sin(2.0 * 3.14159265358979 * 440.0 * n / rate)) : 0;
                pcm[(n % capacity) * channels] = v;
                pcm[(n % capacity) * channels + 1] = v;
            }
            MemoryBarrier();
            InterlockedExchange64(const_cast<volatile LONG64 *>(&ring->written), n);
            Sleep(5);
        }
    });

    uint32_t frames = 0, reserved_fail = 0;
    for (;;)
    {
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        const double t = static_cast<double>(now.QuadPart - started.origin_qpc) /
                         freq.QuadPart;
        if (t >= seconds) break;
        int64_t sample_time = 0;
        if (pace > 0.0 && t < next_frame) { Sleep(1); continue; }
        if (!GpuRecFrameDue(&sample_time)) { Sleep(1); continue; }
        if (pace > 0.0) next_frame = t + jitter[paced++ % 5] / pace;
        Source &src = (resize && t >= seconds / 2) ? other : big;
        // The last copy has to be done before the upload buffer is rewritten.
        if (fence->GetCompletedValue() < fv)
        {
            fence->SetEventOnCompletion(fv, ev);
            WaitForSingleObject(ev, 2000);
        }
        BYTE *mapped = nullptr;
        D3D12_RANGE none = { 0, 0 };
        src.upload->Map(0, &none, reinterpret_cast<void **>(&mapped));
        Fill(mapped + src.fp.Offset, src.fp.Footprint.RowPitch, src.w, src.h, frames,
             sync && marked(sample_time / 1e7));
        src.upload->Unmap(0, nullptr);

        const int slot = GpuRecReserve(src.tex);
        alloc->Reset();
        list->Reset(alloc, nullptr);
        D3D12_TEXTURE_COPY_LOCATION dst = {}, from = {};
        dst.pResource = src.tex;
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        from.pResource = src.upload;
        from.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        from.PlacedFootprint = src.fp;
        list->CopyTextureRegion(&dst, 0, 0, 0, &from, nullptr);
        D3D12_RESOURCE_BARRIER b = {};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = src.tex;
        b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
        list->ResourceBarrier(1, &b);
        if (slot >= 0) GpuRecCopy(list, slot, src.tex);
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
        list->ResourceBarrier(1, &b);
        if (FAILED(list->Close()))
        {
            if (slot >= 0) GpuRecCancel(slot);
            fprintf(stderr, "command list failed\n");
            break;
        }
        ID3D12CommandList *lists[] = { list };
        queue->ExecuteCommandLists(1, lists);
        queue->Signal(fence, ++fv);
        if (slot >= 0) GpuRecSubmit(slot, fence, fv, sample_time);
        else ++reserved_fail;
        ++frames;
    }
    // The tone runs a little past the last frame, as a real capture would.
    Sleep(100);
    stop.store(true);
    tone.join();
    const GpuRecStats st = GpuRecStop();
    fprintf(stderr, "frames %u (no slot %u) -> written %u, dropped %u, audio %u frames, "
                    "codec %u, hr 0x%08X, %u ms\n",
            frames, reserved_fail, st.written, st.dropped, st.audio_frames, st.codec,
            static_cast<unsigned>(st.hr), st.duration_ms);
    printf("{\"written\": %u, \"dropped\": %u, \"codec\": %u, \"audio_frames\": %u, "
           "\"hr\": %u, \"had_audio\": %s, \"submitted\": %u}\n",
           st.written, st.dropped, st.codec, st.audio_frames, static_cast<unsigned>(st.hr),
           st.had_audio ? "true" : "false", frames);
    if (fence->GetCompletedValue() < fv)
    {
        fence->SetEventOnCompletion(fv, ev);
        WaitForSingleObject(ev, 2000);
    }
    return st.hr == S_OK && st.written > 0 ? 0 : 1;
}
