# Aura duration / refresh / pandemic differential probe (aura-lifecycle track B).  `make` regenerates
# the extracted Trinity code from the sibling checkout and builds the probe.  Override TC_ROOT to
# point elsewhere (e.g. `make TC_ROOT=/path/to/TrinityCore`).
TC_ROOT ?= $(abspath ../../../../../TrinityCore)
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable -Wno-unused-parameter
INCLUDES = -I. -I$(TC_ROOT)/src/common

probe: probe.cpp tc_duration_decls.inc tc_duration_bodies.inc
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o probe probe.cpp

tc_duration_decls.inc tc_duration_bodies.inc: extract.py
	python3 extract.py $(TC_ROOT)

clean:
	rm -f probe tc_duration_decls.inc tc_duration_bodies.inc

.PHONY: clean
