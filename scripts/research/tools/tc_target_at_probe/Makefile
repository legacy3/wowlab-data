# AreaTrigger shape-containment differential probe (targeting Track I).  `make`
# regenerates the extracted Trinity code from the sibling checkout and builds the
# probe.  Override TC_ROOT to point elsewhere (e.g. `make TC_ROOT=/path/to/TrinityCore`).
TC_ROOT ?= $(abspath ../../../../../TrinityCore)
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable
INCLUDES = -I. -I$(TC_ROOT)/src/common -I$(TC_ROOT)/src/common/Utilities -I$(TC_ROOT)/src/server/game/Entities/Object -I$(TC_ROOT)/dep/g3dlite/include

probe: probe.cpp tc_at_decls.inc tc_at_bodies.inc
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o probe probe.cpp

tc_at_decls.inc tc_at_bodies.inc: extract.py
	python3 extract.py $(TC_ROOT)

clean:
	rm -f probe tc_at_decls.inc tc_at_bodies.inc

.PHONY: clean
