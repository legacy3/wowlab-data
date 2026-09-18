# Aura same-timestamp ordering / generation probe (aura-lifecycle Track I).  `make` regenerates the
# extracted Trinity code from the sibling checkout and builds the probe.  Override TC_ROOT to point
# elsewhere (e.g. `make TC_ROOT=/path/to/TrinityCore`).
TC_ROOT ?= $(abspath ../../../../../TrinityCore)
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable -Wno-unused-private-field
INCLUDES = -I. -I$(TC_ROOT)/src/common -I$(TC_ROOT)/src/common/Utilities -I$(TC_ROOT)/src/common/Debugging

probe: probe.cpp tc_order_decls.inc tc_order_bodies.inc
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o probe probe.cpp

tc_order_decls.inc tc_order_bodies.inc: extract.py
	python3 extract.py $(TC_ROOT)

clean:
	rm -f probe tc_order_decls.inc tc_order_bodies.inc

.PHONY: clean
