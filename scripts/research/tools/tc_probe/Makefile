# Differential probe.  `make` regenerates the extracted bodies from the sibling
# TrinityCore checkout and builds the probe.
# extract.py resolves the sibling TrinityCore checkout itself; set TC_ROOT to
# override it (e.g. `make TC_ROOT=/path/to/TrinityCore`).
TC_ROOT ?=
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall

probe: probe.cpp tc_extracted.inc
	$(CXX) $(CXXFLAGS) -o probe probe.cpp

tc_extracted.inc: extract.py
	python3 extract.py $(TC_ROOT) $(if $(TC_ROOT),tc_extracted.inc,)
	@test -f tc_extracted.inc

clean:
	rm -f probe tc_extracted.inc

.PHONY: clean
