# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(storyintelligencewidgettest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)

qt_standard_project_setup()

ecm_add_test(
    storyintelligencewidgettest.cpp
    storyintelligencewidget.qrc
    ../../src/story/storyintelligencewidget.h
    ../../src/story/storyintelligencewidget.cpp
    ../../src/prose/proseawarenesswidget.h
    ../../src/prose/proseawarenesswidget.cpp
    ../../src/prose/prosediagnostic.h
    ../../src/theme/chromecolors.h
    ../../src/theme/chromecolors.cpp
    ../../src/theme/theme.h
    ../../src/theme/theme.cpp
    ../../src/theme/themerepository.h
    ../../src/theme/themerepository.cpp
    ../../src/theme/stylesheetbuilder.h
    ../../src/theme/stylesheetbuilder.cpp
    ../../src/theme/svgicontheme.h
    ../../src/theme/svgicontheme.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
            Qt::Widgets
    TEST_NAME storyintelligencewidgettest
)

thothpad_configure_test(storyintelligencewidgettest)

target_include_directories(storyintelligencewidgettest PRIVATE ../../src)

enable_testing(true)
