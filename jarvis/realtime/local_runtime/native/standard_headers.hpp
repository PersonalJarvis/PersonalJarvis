// The pinned engine relies on transitive includes supplied by some standard
// libraries but absent in libc++/MinGW. Keep the build portable explicitly.
#include <algorithm>
#include <cstdlib>
