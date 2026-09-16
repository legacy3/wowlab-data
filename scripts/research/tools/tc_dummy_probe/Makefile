CXX ?= g++
CXXFLAGS ?= -std=c++20 -O0 -ffp-contract=off -Wall

probe: probe.cpp tc_dummy_bodies.inc
	$(CXX) $(CXXFLAGS) -o probe probe.cpp

tc_dummy_bodies.inc: extract.py
	python3 extract.py

.PHONY: run clean
run: probe
	./probe

clean:
	rm -f probe
