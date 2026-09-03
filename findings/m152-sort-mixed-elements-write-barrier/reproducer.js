function changeElementsKindDuringSort(a) {
  function compare() {
    a.fill(0);
    return 0;
  }
  return a.sort(compare);
}

%PrepareFunctionForOptimization(changeElementsKindDuringSort);
for (let i = 0; i < 2; ++i) {
  changeElementsKindDuringSort([1, 2]);
  changeElementsKindDuringSort([{}, {}]);
}
%OptimizeMaglevOnNextCall(changeElementsKindDuringSort);
changeElementsKindDuringSort([1, 2]);

const oldTarget = {value: 0};
gc({type: 'major', execution: 'sync'});

function copyAssumedSmi(source, target) {
  target.value = source[0];
}

%PrepareFunctionForOptimization(copyAssumedSmi);
for (let i = 0; i < 2; ++i) copyAssumedSmi([i, i + 1], oldTarget);
%OptimizeMaglevOnNextCall(copyAssumedSmi);
copyAssumedSmi([1, 2], oldTarget);

const confused = [{}, {}];
changeElementsKindDuringSort(confused);
copyAssumedSmi(confused, oldTarget);
