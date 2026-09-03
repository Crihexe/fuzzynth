// Historical semantic seed from the official M152 regression family.
// It exposes the inconsistent elements kind but intentionally does not crash.
function sortWithNarrowingCallback(a) {
  function compare() {
    a.fill(0);
    return 0;
  }
  return a.sort(compare);
}

%PrepareFunctionForOptimization(sortWithNarrowingCallback);
for (let i = 0; i < 100; ++i) {
  sortWithNarrowingCallback([1, 2]);
  sortWithNarrowingCallback([{}, {}]);
}
%OptimizeMaglevOnNextCall(sortWithNarrowingCallback);
sortWithNarrowingCallback([1, 2]);

const marker = {};
const inconsistent = [marker, {}];
sortWithNarrowingCallback(inconsistent);
print(%HasSmiElements(inconsistent), inconsistent[0] === marker);
