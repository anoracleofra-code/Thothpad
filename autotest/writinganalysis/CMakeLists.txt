# SPDX-License-Identifier: GPL-3.0-or-later
find_package(Qt6 REQUIRED COMPONENTS Core Test)
ecm_add_test(writinganalysistest.cpp ../../src/statistics/writinganalysis.cpp
    LINK_LIBRARIES Qt::Core Qt::Test TEST_NAME writinganalysistest)
target_include_directories(writinganalysistest PRIVATE ../../src)
thothpad_configure_test(writinganalysistest)
