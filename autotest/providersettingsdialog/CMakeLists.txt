# SPDX-License-Identifier: GPL-3.0-or-later
find_package(Qt6 REQUIRED COMPONENTS Core Gui Network Test Widgets)

ecm_add_test(
    providersettingsdialogtest.cpp
    ../../src/prose/providersettingsdialog.cpp
    ../../src/prose/providersettingsdialog.h
    ../../src/prose/credentialstore.cpp
    ../../src/prose/credentialstore.h
    ../../src/prose/writerengineclient.cpp
    ../../src/prose/writerengineclient.h
    ../../src/prose/performancepolicy.cpp
    ../../src/messageboxhelper.cpp
    LINK_LIBRARIES Qt::Core Qt::Gui Qt::Network Qt::Test Qt::Widgets
    TEST_NAME providersettingsdialogtest
)
thothpad_configure_test(providersettingsdialogtest)
target_compile_definitions(providersettingsdialogtest PRIVATE APPVERSION="test")
