# R2 hostile-review probe (aura-lifecycle): refresh entry points and duration mutations vs the tick budget.
TC_ROOT ?= $(abspath ../../../../../TrinityCore)
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable -Wno-unused-parameter
INCLUDES = -I. -I$(TC_ROOT)/src/common

probe: probe.cpp tc_r2_decls.inc tc_r2_bodies.inc tc_r2_extra.inc
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o probe probe.cpp

tc_r2_decls.inc tc_r2_bodies.inc tc_r2_extra.inc: extract.py ../tc_aura_periodic_probe/extract.py
	python3 extract.py $(TC_ROOT)

clean:
	rm -f probe tc_r2_decls.inc tc_r2_bodies.inc tc_r2_extra.inc

.PHONY: clean
