# SPDX-License-Identifier: GPL-3.0-or-later
find_package(Qt6 REQUIRED COMPONENTS Core Test Widgets)
ecm_add_test(storyworkspacetest.cpp
    ../../src/story/storyworkspace.cpp
    ../../src/story/storyworkspacedialog.cpp
    LINK_LIBRARIES Qt::Core Qt::Test Qt::Widgets
    TEST_NAME storyworkspacetest)
target_include_directories(storyworkspacetest PRIVATE ../../src)
thothpad_configure_test(storyworkspacetest)
