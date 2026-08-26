# ThothPad Unit Tests

ThothPad unit tests use the Qt Test library. Run them through CTest
with Qt's offscreen platform so GUI tests cannot open windows during a test run.

## Prerequisites

Before the unit tests can be built and run, you must install the following on your operating system:

* cmake (version 3.16 or later)
* Qt 5.15 or later
* Qt modules (see main README.md in root directory for Qt module dependencies)

## Linux and MacOS

To build and run all tests on Linux, enter the following commands:

    $ cd <ghostwriter path>
    $ cd test
    $ mkdir build
    $ cd build
    $ cmake ..
    $ make
    $ QT_QPA_PLATFORM=offscreen ctest

To run with verbose output of each assertion check, run `ctest`  with the `-V` parameter.

    $ QT_QPA_PLATFORM=offscreen ctest -V

## Windows

    > cd <ghostwriter path>
    > cd test
    > mkdir build
    > cd build
    > cmake -G"NMake Makefiles" ..
    > nmake
    > $env:QT_QPA_PLATFORM = "offscreen"
    > ctest

From the repository root, the supported launcher is:

    > .\autotest\run-tests.ps1 -BuildDirectory build-windows-craft

Do not launch the individual Qt test executables directly. CTest supplies the
Qt runtime and offscreen platform-plugin environment before each process starts.

To run with verbose output of each assertion check, run `ctest`  with the `-V` parameter.

    $ ctest -V

## Native efficiency collector

Do not launch Qt test executables directly. Use the benchmark launcher, which
builds the collector and invokes it through CTest with the platform plugin path
and `QT_QPA_PLATFORM=offscreen` configured before `QApplication` starts:

    python benchmarks/run_external_collector.py --build-dir <cmake-build> --suite smoke

Use `--suite certification` only on a dedicated benchmark runner. It executes
30 warm and 20 document-open cold trials per fixture and emits raw JSON without
claiming that the build is certified.
