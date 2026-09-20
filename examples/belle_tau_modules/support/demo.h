#pragma once
#include "eventweight.h"
#include <vector>

// Demonstration only: replace with the calibrated weights for your analysis.
inline EventWeight demo_weight(1.0);

inline double mass_offset(std::vector<double> values) {
    return values.at(0) - 1.777;
}
