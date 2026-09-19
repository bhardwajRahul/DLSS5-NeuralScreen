"""Execute the production send block: one prepared capture, one guide update.

No GPU needed. The worker/guide boundaries are mocked; the actual main-loop
statements are compiled from main.py so duplicate calls cannot hide in a helper.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    tree = ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    blocks = [node for node in ast.walk(tree) if isinstance(node, ast.Try)
              and node.body and isinstance(node.body[0], ast.Expr)
              and isinstance(node.body[0].value, ast.Call)
              and isinstance(node.body[0].value.func, ast.Name)
              and node.body[0].value.func.id == 'check_worker']
    assert len(blocks) == 1, 'worker/guide send path runs twice per frame'
    loop = ast.For(target=ast.Name(id='_frame', ctx=ast.Store()),
                   iter=ast.List(elts=[ast.Constant(0)], ctx=ast.Load()),
                   body=blocks, orelse=[])
    code = compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])),
                   'production-send-block', 'exec')
    for hardware, bypass in ((False, False), (True, False), (True, True)):
        calls = []
        frame = object()
        guide = SimpleNamespace(motion=object(), reset=False)
        def capture(*args):
            calls.append('capture')
        def process(*args, **kwargs):
            calls.append('guides')
            assert calls == ['capture', 'guides']
            assert kwargs['gray'] is frame
            assert kwargs['compute_motion'] is not hardware
            return guide
        guides = SimpleNamespace(process=Mock(side_effect=process),
                                 zero_guide=Mock(return_value=guide), previous_gray=object())
        worker = object()
        st = SimpleNamespace(worker=worker, worker_logs=[], reader=object(),
             cfg={'motion_backend': 'nvofa'}, gray_active=True, frame_index=7, pts=70,
             guides=guides, shm=SimpleNamespace(read_gray=lambda: frame), work_frame=None,
             pending_shot=None, recorder=None, motion_small=True, dda_mode=True,
             split_pos=0, lang='en', guide_fails=0)
        status = SimpleNamespace(failed=False, worker=worker, update=lambda *a: hardware)
        send = Mock()
        namespace = dict(st=st, bypass=bypass, motion_status=status, time=time, sys=sys,
                         check_worker=Mock(), prepare_capture=capture, send_frame=send,
                         _perf=Mock())
        exec(code, namespace)
        assert calls == (['capture'] if bypass else ['capture', 'guides'])
        assert send.call_count == 1
        assert send.call_args.kwargs['prepared'] is True
        assert guides.process.call_count == (0 if bypass else 1)
        if bypass:
            assert guides.previous_gray is None
            guides.zero_guide.assert_called_once()

    # Bypass WITH Frame Generation: the presenter interpolates the frames it is
    # handed, so the guides stop being disposable there. Zero motion and a
    # per-frame reset is exactly what left the feature with nothing to
    # interpolate (#104) - and the reset also reaches FG, which resets on
    # `fh.reset` regardless of the bypass flag.
    frame = object()
    guide = SimpleNamespace(motion=object(), reset=False)
    calls = []
    def process(*args, **kwargs):
        calls.append('guides')
        return guide
    guides = SimpleNamespace(process=Mock(side_effect=process),
                             zero_guide=Mock(return_value=guide), previous_gray=object())
    worker = object()
    st = SimpleNamespace(worker=worker, worker_logs=[], reader=object(),
         cfg={'motion_backend': 'nvofa', 'frame_generation': True}, gray_active=True,
         frame_index=7, pts=70, guides=guides, shm=SimpleNamespace(read_gray=lambda: frame),
         work_frame=None, pending_shot=None, recorder=None, motion_small=True,
         dda_mode=True, split_pos=0, lang='en', guide_fails=0)
    status = SimpleNamespace(failed=False, worker=worker, update=lambda *a: True)
    send = Mock()
    namespace = dict(st=st, bypass=True, motion_status=status, time=time, sys=sys,
                     check_worker=Mock(), prepare_capture=Mock(), send_frame=send,
                     _perf=Mock())
    exec(code, namespace)
    assert guides.process.call_count == 1, \
        'bypass + FG must compute real guides: zero motion and reset=True leave FG nothing to interpolate'
    assert guides.zero_guide.call_count == 0, \
        'bypass + FG fell back to zero_guide: the field FG interpolates from is zero and reset'
    assert send.call_args.args[4] is False, \
        'the reset flag still reaches the worker every bypass frame; FG resets on fh.reset too'
    print('PASS: capture precedes one guide update; NVOFA skips DIS; bypass skips guides '
          'unless FG is presenting them')


if __name__ == '__main__':
    main()
