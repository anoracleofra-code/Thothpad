find_package(Qt6 REQUIRED COMPONENTS Core Network Test)
find_package(Qt6Keychain REQUIRED)

ecm_add_test(
    opencodeconnectiontest.cpp
    LINK_LIBRARIES Qt::Core Qt::Network Qt::Test qt6keychain
    TEST_NAME opencodeconnectiontest
)
thothpad_configure_test(opencodeconnectiontest)
