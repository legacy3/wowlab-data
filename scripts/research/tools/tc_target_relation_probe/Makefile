# Relation-predicate differential probe (Track B).  `make` regenerates the extracted
# Trinity code from the sibling checkout and builds the probe.  Override TC_ROOT
# to point elsewhere (e.g. `make TC_ROOT=/path/to/TrinityCore`).
TC_ROOT ?= $(abspath ../../../../../TrinityCore)
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable
INCLUDES = -I. -I$(TC_ROOT)/src/common/Utilities

probe: probe.cpp tc_relation_decls.inc tc_relation_bodies.inc
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o probe probe.cpp

tc_relation_decls.inc tc_relation_bodies.inc: extract.py
	python3 extract.py $(TC_ROOT)

clean:
	rm -f probe tc_relation_decls.inc tc_relation_bodies.inc

.PHONY: clean
