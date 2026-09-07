# SPDX-License-Identifier: GPL-3.0-or-later
find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)
ecm_add_test(
    lensliststest.cpp
    ../../src/prose/lenslistswidget.h
    ../../src/prose/lenslistswidget.cpp
    ../../src/prose/profileeditordialog.h
    ../../src/prose/profileeditordialog.cpp
    ../../src/messageboxhelper.cpp
    LINK_LIBRARIES Qt::Core Qt::Gui Qt::Test Qt::Widgets
    TEST_NAME lensliststest
)
thothpad_configure_test(lensliststest)
