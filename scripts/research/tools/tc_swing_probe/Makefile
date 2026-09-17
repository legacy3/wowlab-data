# Differential probe for weapon_combat part D (swing scheduling + attack table).
# `make` regenerates the verbatim TrinityCore bodies and builds the probe.
# extract.py resolves the sibling TrinityCore checkout; override with TC_ROOT.
TC_ROOT ?=
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall -Wno-unused-variable -Wno-unused-function

probe: probe.cpp tc_swing_bodies.inc
	$(CXX) $(CXXFLAGS) -o probe probe.cpp

tc_swing_bodies.inc: extract.py
	python3 extract.py $(TC_ROOT) $(if $(TC_ROOT),tc_swing_bodies.inc,)
	@test -f tc_swing_bodies.inc

clean:
	rm -f probe tc_swing_bodies.inc

.PHONY: clean
