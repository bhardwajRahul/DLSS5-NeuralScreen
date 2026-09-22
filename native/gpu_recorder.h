// GPU recording: the worker encodes the picture it presents, on the card.
//
// The old path pulled every recorded frame back to Python - 33 MB at 4K: a
// readback, a copy through shared memory, an RGBA->YUV conversion on the CPU
// (19.9 ms a frame, measured) and PyAV's hand-off to NVENC - and recording
// cost about a third of the frame rate even at 30 fps. Here the frame never
// leaves the GPU:
//
//   worker thread   one CopyResource(output -> ring slot) per recorded frame,
//                   a small submission of its own on the worker's queue
//   encoder thread  waits for that copy's fence, converts RGBA -> NV12 with
//                   the D3D11 video processor (scaling and letterboxing a
//                   frame whose size changed mid-recording), and hands the
//                   NV12 surface to a Media Foundation sink writer: NVIDIA's
//                   hardware encoder (NVENC) behind Windows' own API, so
//                   nothing beyond the Windows SDK is needed to build it
//   audio           the client (Python) captures the system sound and writes
//                   16-bit PCM into a shared ring (AudioRingHeader); the
//                   encoder thread feeds it to the same writer as AAC, so the
//                   file is complete when the writer finalizes - there is no
//                   remux of a multi-gigabyte file at the end
//
// The file is fragmented MP4 where the system offers it: a worker that dies
// mid-recording still leaves a file that plays up to its last fragment.
//
// Threading: GpuRecStart/FrameDue/Reserve/Copy/Submit/Cancel/Stop are called
// from the worker's main thread only; everything Media Foundation touches
// lives on the encoder thread.
#pragma once

#include <d3d12.h>
#include <cstdint>

// Codec requests on the wire (RECS.codec) and in the answer (RSAK.codec).
enum : uint32_t
{
    GPUREC_CODEC_AUTO = 0,   // AV1, then HEVC, then H.264: the first that opens
    GPUREC_CODEC_H264 = 1,
    GPUREC_CODEC_HEVC = 2,
    GPUREC_CODEC_AV1  = 3,
};

// The PCM ring the client writes and the encoder thread reads. The client
// creates the named mapping: this header, then capacity * channels int16
// samples, interleaved. `written` counts frames (one sample per channel)
// ever written; the client stores the samples first and the count last.
#pragma pack(push, 1)
struct GpuRecAudioRing
{
    uint32_t magic;                // GPUREC_AUDIO_MAGIC
    uint32_t rate, channels, capacity;
    volatile int64_t written;
    int64_t reserved;
};
#pragma pack(pop)
static_assert(sizeof(GpuRecAudioRing) == 32, "GpuRecAudioRing != AUDIO_RING_FMT");
static constexpr uint32_t GPUREC_AUDIO_MAGIC = 0x474E5241u;   // "ARNG"

struct GpuRecParams
{
    const wchar_t *path;         // the MP4 to write (created, then finalized)
    UINT width, height;          // the recording size; odd sizes are rounded down
    UINT fps;                    // the stream's frame rate: one frame per slot
    uint32_t codec;              // GPUREC_CODEC_*
    uint32_t bitrate;            // bits per second; 0 = chosen from size and rate
    int64_t start_qpc;           // when the audio ring's frame 0 was (QueryPerformanceCounter)
    const char *audio_name;      // the client's PCM ring, or nullptr for no sound
};

struct GpuRecStarted
{
    HRESULT hr;                  // why it did not start, when it did not
    uint32_t codec;              // GPUREC_CODEC_* in use
    bool audio;                  // whether the file has a sound track
    int64_t origin_qpc;          // the file's time 0 (QueryPerformanceCounter)
    UINT width, height, fps;     // as recorded (odd sizes rounded down)
    uint32_t bitrate;
};

struct GpuRecStats
{
    uint32_t written;            // video frames handed to the encoder
    uint32_t dropped;            // slots that had no free surface
    uint32_t codec;              // GPUREC_CODEC_* actually used
    uint32_t audio_frames;       // PCM frames written (0 without audio)
    HRESULT hr;                  // the first failure, S_OK when there was none
    uint32_t duration_ms;        // wall-clock length of the recording
    bool had_audio;
};

// Start a recording. Returns false (and why, in *hr) when no encoder, no
// device or no file could be set up - the client then falls back to the
// old path. A recording already running is stopped first.
bool GpuRecStart(ID3D12Device *dev, const GpuRecParams &params, GpuRecStarted *out);
bool GpuRecActive();
// The encoder hit an error: nothing more will be recorded (see GpuRecStop).
bool GpuRecFailed();

// Whether a new frame slot has begun since the last recorded frame: the
// clock decides, exactly like the old recorder's needs_frame(). Returns the
// slot's timestamp (100 ns units from the origin) and reserves the slot.
bool GpuRecFrameDue(int64_t *sample_time);

// A free ring slot sized for this frame, or -1 when the encoder has fallen
// behind (the frame is counted as dropped).
int GpuRecReserve(ID3D12Resource *src);

// Record CopyResource(src -> slot) into `list`. `src` must already be in
// COPY_SOURCE. Call between BeginCommands() and EndCommands().
void GpuRecCopy(ID3D12GraphicsCommandList *list, int slot, ID3D12Resource *src);

// The copy was submitted: the encoder takes the slot once `fence` reaches
// `value`. Or it was not (the list failed): hand the slot back.
void GpuRecSubmit(int slot, ID3D12Fence *fence, UINT64 value, int64_t sample_time);
void GpuRecCancel(int slot);

// Stop: drain, finalize the file, release everything. Safe when idle.
GpuRecStats GpuRecStop();
