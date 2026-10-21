#include "squadpad/playback.hpp"

// Compile the same implementation verified by the portable test runner. Keep
// one source of truth and no copied variant. UBT discovers this wrapper under
// Source; the standard C++ runner still compiles RuntimeCore/src directly.
#include "../../../RuntimeCore/src/playback.cpp"
