# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(outlinewidgettest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Concurrent Core Gui Test Widgets)

qt_standard_project_setup()

if (MSVC)
    set(CMARK_TEST_LIBS libcmark-gfm libcmark-gfm-extensions)
else()
    set(CMARK_TEST_LIBS libcmark-gfm_static libcmark-gfm-extensions_static)
endif()

ecm_add_test(
    outlinewidgettest.cpp
    ../../src/editor/actions/inlinemarkuptoggle.cpp
    ../../src/editor/markdowndocument.cpp
    ../../src/editor/markdowneditor.cpp
    ../../src/editor/breathmapwidget.cpp
    ../../src/editor/markdownhighlighter.cpp
    ../../src/editor/textformatoverlaycontroller.cpp
    ../../src/markdown/cmarkgfmapi.cpp
    ../../src/markdown/markdownast.cpp
    ../../src/markdown/markdownnode.cpp
    ../../src/outlinewidget.cpp
    ../../src/statistics/documentstatistics.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Concurrent
            Qt::Gui
            Qt::Test
            Qt::Widgets
            ${CMARK_TEST_LIBS}
    TEST_NAME outlinewidgettest
)

thothpad_configure_test(outlinewidgettest)

target_include_directories(outlinewidgettest PRIVATE
    ../..
    ../../src
    ../../3rdparty/cmark-gfm/src
)

enable_testing(true)
