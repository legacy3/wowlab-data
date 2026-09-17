# Chain / smart-selection / RNG-helper differential probe (targeting Track D).
# `make` regenerates the extracted Trinity code from the sibling checkout and
# builds the probe.  Override TC_ROOT to point elsewhere.
TC_ROOT ?= $(abspath ../../../../../TrinityCore)
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable
INCLUDES = -I. -I$(TC_ROOT)/src/common -I$(TC_ROOT)/src/common/Utilities

probe: probe.cpp tc_chain_decls.inc tc_chain_bodies.inc tc_chain_position.inc
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o probe probe.cpp

tc_chain_decls.inc tc_chain_bodies.inc tc_chain_position.inc: extract.py
	python3 extract.py $(TC_ROOT)

clean:
	rm -f probe tc_chain_decls.inc tc_chain_bodies.inc tc_chain_position.inc

.PHONY: clean
