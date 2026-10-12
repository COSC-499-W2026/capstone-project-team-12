# Week 4 Individual Logs
## Eddy Tian

(Sept. 27th - Oct. 4th)
Tasks Completed:
Issue [[#120](https://github.com/COSC-499-W2026/capstone-project-team-12/issues/120)]:
For requirement A10 (extracting text from a photo to verify the room sign), EasyOCR is our riskiest dependency because of PyTorch's size, the model download and arm64 support. I checked early that it runs inside our API image and that it can read real UBCO room signs. I split the issue into two PRs so each one stays small and easy to review: one for getting EasyOCR working in the image, and one for reporting the results on real photos.

PR [[#136](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/136)]:
I got EasyOCR 1.7.2 running in the API image on CPU-only PyTorch. The Dockerfile fails the build if a CUDA build of PyTorch slips in, downloads the OCR models at build time, and then reloads them with downloads disabled, so the build fails if the models are not really baked in. This means no request ever downloads a model, and the image works offline as the technical specification requires. I added 5 smoke tests (CPU-only torch, models present, a missing model fails instead of downloading, a rendered sign is read with the restricted character set, and other characters are blocked). They are skipped unless the OCR models are present, so CI never downloads them. I built and tested the image on amd64 (2.31 GB) and Mia built and tested on arm64 (MacBook Air M1, 1.91 GB, 95.6 s build), with all 5 tests passing on both.

My initial PR was reviewed by Mia, who identified several small problems: the CI cache key did not include `requirements.txt` even though torch is in it, a few lines in `requirements.txt` had moved and added noise to the diff, the PR was not linked to the issue, and one test depends on EasyOCR's error text. I added `requirements.txt` to the cache key, restored the original line order, linked the PR to #120, and left a note in the PR about the error-text test, since it may need updating when EasyOCR is upgraded.

This work is written in the Dockerfile and `requirements.txt` because the image installs only that file, and the tests are in `tests/backend/test_ocr_smoke.py` with the other backend tests. It belongs in the OCR step of the verification process (3.0) in the DFD, since it provides the OCR engine that component will call. It does not change the DFD. This PR did not break anything else in the system, because the new tests are skipped outside the image and ruff, mypy and pytest all passed. The PR only contains small functions (3 to 8 lines) and changed 5 files, about 120 lines in total.

PR [[#140](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/140)]:
I measured EasyOCR on 11 real room-sign photos inside the API image and wrote up the results in `docs/ocr-spike.md`. I wrote a script, `utils/ocr_spike.py`, that applies the specification's photo pipeline (resize, grayscale, auto-contrast, restricted character set) and prints the result, time and memory for each photo, so the team can repeat the measurements on any machine. The main findings were:

* 10 of 11 signs were read correctly. The one miss was a blurred photo.
* OCR took a median of 3.62 s per photo (3.14 to 4.47 s), under the specification's 6 s target, though on a 16-CPU machine and not the 4 vCPUs the specification assumes.
* Peak memory was 1660 MiB for one process. For the 2 concurrent OCR jobs the design allows, that works out to about 2.4 GiB if the jobs share one loaded reader and up to about 3.2 GiB if each loads its own, above the specification's 2 GB estimate. The load test should measure this.
* The photos were taken with the phone camera app and not the in-app camera, so accuracy is overstated and the real MATCH rate has to come from the OCR evaluation.
* `requirements.txt` pulls some pinned packages from the PyTorch index as well as PyPI, which is why every pin has to stay exact.

CI caught a problem in this PR: mypy failed because EasyOCR has no type information. Instead of silencing mypy everywhere, I added a mypy override for only the `easyocr` module in `pyproject.toml`, which is what the first PR said the first code importing EasyOCR would need.

Mia reviewed this PR and suggested several improvements, which I evaluated and made:

* Tests for the parts of the script that do not need the model. I added `tests/backend/test_ocr_spike.py` with 18 test cases for `normalise`, `candidates`, `format_row` and `load_labels`, using fixed text boxes in place of OCR output. They include the case of "COM" and "316" in neighbouring boxes becoming COM316, which is the same joining the specification needs in the verification service. There are also negative cases (a sign with only text, a number without letters, too few or too many digits, boxes that are not neighbours). To check that the tests can fail, I broke the script in 7 ways (for example removing the joining and keeping hyphens) and each one made at least one test fail. Adding the tests also found a problem: the script imported `resource`, which does not exist on Windows, so `pytest` would have failed for teammates on Windows. I moved that import into the one function that uses it.
* I added `"../../utils"` to the pytest `pythonpath` in `pyproject.toml` so the tests can import the script.
* I rewrote the memory risk to say directly that two concurrent jobs probably need more than 2 GB. I did not use the suggested 3.3 GB figure as the main number, because it assumes each job loads its own copy of the models. I gave the range of 2.4 to 3.2 GiB and noted that it is calculated from one job and not measured.
* I added a concrete next step for the torch threads: try `torch.set_num_threads(2)` on a 4 vCPU server and compare the p95 time in the load test, checking that the setting takes effect in the worker threads that run OCR.
* I corrected the Size section of the PR description, which still said 2 files.
* I committed the photo labels as `docs/ocr-spike-labels.csv` because it contains only file names and room numbers, so the run is easier to reproduce. The photos themselves stay out of the repository because some show people through the glass.

After these changes, Mia suggested 2 more minor, non-blocking errors, that I decided not to commit in the interest of time.

This work is written in `utils/`, `docs/` and `tests/backend/` because the script is a standalone tool and not application code, the issue asks for the findings in `docs/`, and all backend tests live in `tests/backend/`. It belongs to the same OCR step of the verification process (3.0) in the DFD and does not change it. The 18 test cases are all unit tests, and integration tests are not required because the functions only transform text and do not touch the model, the database or the API. This PR did not break anything else in the system, since it only adds new files and a mypy override and a pytest path for the new module. The PR only contains small functions (3 to 18 lines in the script, 2 to 4 lines in the tests) and changed 5 files, about 430 lines in total.

Otherwise, the photos used for the evaluation were kept out of the repository because some show people through the glass, and the findings note lists the open risks for the team to consider when building the verification pipeline.

