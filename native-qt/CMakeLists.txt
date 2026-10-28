cmake_minimum_required(VERSION 3.16)
project(agent_usage_qt VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets Gui Core)

add_executable(agent-usage-qt
    main.cpp
)

target_link_libraries(agent-usage-qt PRIVATE
    Qt6::Widgets
    Qt6::Gui
    Qt6::Core
)

install(TARGETS agent-usage-qt RUNTIME DESTINATION bin)
