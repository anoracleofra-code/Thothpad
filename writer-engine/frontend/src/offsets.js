export function selectionRange(flag) {
  return [flag.start_utf16 ?? flag.start, flag.end_utf16 ?? flag.end];
}
