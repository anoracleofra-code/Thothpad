# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(efficiencycollector VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Concurrent Core Gui Test Widgets)

qt_standard_project_setup()

if (MSVC)
    set(CMARK_TEST_LIBS libcmark-gfm libcmark-gfm-extensions)
else()
    set(CMARK_TEST_LIBS libcmark-gfm_static libcmark-gfm-extensions_static)
endif()

ecm_add_test(
    efficiencycollector.cpp
    ../../src/editor/actions/inlinemarkuptoggle.cpp
    ../../src/editor/markdowndocument.cpp
    ../../src/editor/markdowneditor.cpp
    ../../src/editor/markdownhighlighter.cpp
    ../../src/editor/textformatoverlaycontroller.cpp
    ../../src/markdown/cmarkgfmapi.cpp
    ../../src/markdown/markdownast.cpp
    ../../src/markdown/markdownnode.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Concurrent
            Qt::Gui
            Qt::Test
            Qt::Widgets
            ${CMARK_TEST_LIBS}
    TEST_NAME efficiencycollector
)

# KDECompilerSettings disables C++ exceptions by default, while this standalone
# benchmark collector deliberately uses exceptions to propagate measurement and
# fixture failures to its top-level error reporter.
kde_target_enable_exceptions(efficiencycollector PRIVATE)

thothpad_configure_test(efficiencycollector)
set_tests_properties(efficiencycollector PROPERTIES
    LABELS "benchmark;native-efficiency"
    SKIP_RETURN_CODE 77
    TIMEOUT 21600
)

target_include_directories(efficiencycollector PRIVATE
    ../..
    ../../src
    ../../3rdparty/cmark-gfm/src
)

enable_testing(true)
