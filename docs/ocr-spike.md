# Spike: EasyOCR in the API image (issue #120)

Check made early because EasyOCR is the riskiest dependency (PyTorch, model download, arm64).
The image changes are in the first PR for this issue; this note records what we measured and the
risks we found. The numbers come from the build log, from running `utils/ocr_spike.py` (see
"How to reproduce"), and from the first PR's image checks on amd64 and arm64.

## Build (linux/amd64)

Source: the `docker compose build --no-cache --progress=plain api` log from a Windows machine
(x86-64 wheels, `supercronic-linux-amd64`). The `python:3.11-slim` base layer was already cached.
The log has no overall wall-clock time, so the total is the sum of its step times.

| Step | Time |
|---|---|
| `pip install -r requirements.txt` (CPU-only torch 2.8.0, torchvision 0.23.0, EasyOCR 1.7.2) | 74.8 s |
| Bake EasyOCR models (download, then reload with downloads disabled) | 19.0 s |
| Export image layers and unpack into Docker | 40.8 s |
| Other steps (supercronic download, copy) | about 1 s |
| **Total** | **about 136 s (2 min 16 s)** |

- The CPU-only wheel is `torch-2.8.0+cpu`, 184.1 MB to download; the `torch.version.cuda is None`
  check in the Dockerfile passed.
- Image size on amd64: 2.31 GB.

## Build (linux/arm64)

From the first PR, on a MacBook Air M1 (macOS 15.6.1): the image builds with no package problems
and the 5 smoke tests pass (5.36 s). The base image was cached, as in the amd64 log.

| Step | Time |
|---|---|
| `pip install -r requirements.txt` | 46.2 s |
| Bake EasyOCR models | 13.2 s |
| **Total build** | **95.6 s** |

- Image size on arm64: 1.91 GB on disk (466 MB of content).
- The OCR timings below are from amd64 only. The sign photos were not read on the arm64 image.

## Real sign photos

11 phone photos of UBCO room signs, 2304 x 4096 px, taken in two buildings (COM 301 to 316, LIB 308
to 332). They include the hard cases the spec worries about: a blurred sign (COM 304), a small sign
far from the camera (LIB 314), signs behind glass with reflections, and signs on a booking-policy
notice full of other text. The photos are not committed: some show people through the glass.

Run inside the API image on x86-64 with 16 CPUs (torch used 8 threads), EasyOCR 1.7.2 and torch
2.8.0+cpu, with downloads disabled. Each photo was resized to 1600 px, converted to grayscale and
auto-contrasted (as the spec describes), then read with the restricted character set (digits, A-Z,
space, hyphen).

| photo | expected | read | confidence | correct | boxes | prepare s | OCR s | peak RSS MiB |
|---|---|---|---|---|---|---|---|---|
| 1 - 6BM6O3o.jpg | COM316 | COM316 | 0.92 | yes | 59 | 0.11 | 3.79 | 1629 |
| 2 - 3fA5Vvt.jpg | COM312 | COM312 | 0.91 | yes | 54 | 0.10 | 3.74 | 1629 |
| 3 - OdsT5fV.jpg | COM311 | COM311 | 0.76 | yes | 59 | 0.10 | 3.70 | 1646 |
| 4 - makS7v0.jpg | COM310 | COM310 | 0.93 | yes | 91 | 0.11 | 4.47 | 1646 |
| 5 - FFSEcqY.jpg | COM306 | COM306 | 1.00 | yes | 22 | 0.10 | 3.62 | 1646 |
| 6 - MwLRYYR.jpg | COM304 | - | - | no | 3 | 0.10 | 3.19 | 1647 |
| 7 - Olb4QnD.jpg | COM301 | COM301 | 0.65 | yes | 35 | 0.11 | 3.68 | 1660 |
| 8 - A7cEpcB.jpg | LIB312 | LIB312 | 1.00 | yes | 14 | 0.11 | 3.33 | 1660 |
| 9 - 5nuYP8l.jpg | LIB308 | LIB308 | 0.70 | yes | 1 | 0.10 | 3.26 | 1660 |
| 10 - dCWtWcz.jpg | LIB314 | LIB314 | 0.94 | yes | 6 | 0.11 | 3.20 | 1660 |
| 11 - QBdAoyt.jpg | LIB332 | LIB332 | 0.99 | yes | 1 | 0.11 | 3.14 | 1660 |

| Measure | Result |
|---|---|
| Photos read correctly | 10 of 11 |
| OCR time per photo | median 3.62 s, min 3.14 s, max 4.47 s |
| Preparation time per photo (resize, grayscale, contrast) | 0.10 to 0.11 s |
| First read after loading the reader (warm-up) | 3.96 s |
| Reader load time with downloads disabled | 1.53 s |
| Peak memory (RSS) after loading the reader | 853 MiB |
| Peak memory (RSS) after all photos | 1660 MiB |

- **The one miss** is the blurred COM 304 photo: EasyOCR found only 3 text boxes and no room number.
  The spec's decision table gives NO_MATCH for this, and the student has 6 attempts to retake it.
- **Time is mostly a fixed cost.** A photo with 1 text box took 3.14 s and one with 91 boxes took
  4.47 s, so a notice full of text adds about 1.3 s.
- **Confidence on correct reads** ranged from 0.65 to 1.00. COM 301 (0.65) is only just above the
  starting MATCH threshold of 0.60, and COM 311 (0.76) and LIB 308 (0.70) are not far above it.
  These starting values will be calibrated with the OCR evaluation, not from these 11 photos.
- **Against the spec targets:** every photo took under 6 s (the slowest was 4.47 s of OCR plus
  0.11 s of preparation) with no queue. This is a single run on a 16-CPU machine, not a p95, and
  the spec measures on 4 vCPUs, so expect slower reads there.

## Risks and findings

- **These photos overstate accuracy.** They are full-resolution shots from the phone camera app.
  The spec says evaluation photos must come from the in-app camera (720p to 1080p, no HDR or focus
  processing). Treat 10 of 11 as a smoke check; the real MATCH rate comes from the OCR evaluation
  with in-app frames.
- **Two concurrent jobs probably need more than the spec's 2 GB.** One job used 1660 MiB: 853 MiB
  after loading the reader, plus about 807 MiB while reading photos. The spec allows 2 OCR jobs at
  once and estimates about 2 GB for EasyOCR. If both jobs share one loaded reader, that is about
  2.4 GiB (853 + 2 x 807 MiB); if each job loads its own, up to about 3.2 GiB (2 x 1660 MiB). Both
  figures are calculated from the single-job run, not measured, and leave out PostgreSQL, the rest
  of the API and the operating system. Share one reader between jobs, measure memory with 2
  concurrent jobs in the load test, and size the server from that.
- **Torch uses many threads by default.** Torch used 8 threads on this machine. With 2 concurrent
  jobs they compete for the same CPUs, which affects the p95 target under load (load test
  scenario 2, 10 simultaneous uploads). Next step: on a 4 vCPU server try
  `torch.set_num_threads(2)` so that 2 jobs use 4 threads in total (check that the setting takes
  effect in the worker threads that run OCR), and compare p95 with and without it in the load test.
- **Pinned packages come from two indexes.** `requirements.txt` uses `--extra-index-url` for the
  PyTorch CPU wheels. The build log shows `Jinja2 3.1.6` and `MarkupSafe 3.0.3` were downloaded from
  `download.pytorch.org`, not PyPI. Versions are pinned, but pip may pick either index for any
  package, so keep every pin exact and review new `requirements.txt` lines.
- **The build needs GitHub.** EasyOCR downloads its models from GitHub release assets during the
  build (19 s in the log above). If GitHub is unreachable the image cannot be built; once built it
  works offline, as the spec requires.
- **Smoke test uses a synthetic sign.** `tests/backend/test_ocr_smoke.py` renders text with Pillow;
  it proves the models load and the allowlist works, not accuracy on real signs.

## How to reproduce

Put the photos in one folder and copy `docs/ocr-spike-labels.csv` into it as `labels.csv`. It lists
the expected room number for each of the 11 photos (columns `file,expected`, for example `COM316`),
so photo files must keep their original names. The photos are not in the repo (some show people
through the glass); to use your own, list them in a `labels.csv` the same way. Then, from the repo
root (the first command builds the image if it is missing):

```bash
docker compose build api
docker compose run --rm --no-deps \
    -v ./utils:/utils:ro -v /path/to/photos:/photos:ro api \
    python /utils/ocr_spike.py /photos
docker image ls hunt-api:dev
```

On Git Bash for Windows, prefix the `docker compose run` line with `MSYS_NO_PATHCONV=1`. Results
depend on the machine, so note which one each run is from.
