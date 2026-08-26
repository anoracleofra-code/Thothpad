# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(spellcheckdecoratortest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)
find_package(KF6 REQUIRED COMPONENTS Sonnet)

qt_standard_project_setup()

ecm_add_test(
    spellcheckdecoratortest.cpp
    ../../src/spelling/spellcheckdecorator.h
    ../../src/spelling/spellcheckdecorator.cpp
    ../../src/spelling/spellcheckdialog.h
    ../../src/spelling/spellcheckdialog.cpp
    ../../src/editor/textformatoverlaycontroller.h
    ../../src/editor/textformatoverlaycontroller.cpp
    ../../src/messageboxhelper.h
    ../../src/messageboxhelper.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
            Qt::Widgets
            KF6::SonnetCore
            KF6::SonnetUi
    TEST_NAME spellcheckdecoratortest
)

thothpad_configure_test(spellcheckdecoratortest)

target_include_directories(spellcheckdecoratortest PRIVATE ../../src)

enable_testing(true)
