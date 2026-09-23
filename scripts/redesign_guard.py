#!/usr/bin/env python3
"""Presentation-only guard for the Stoic redesign.

Compares each changed Swift file against a base commit and FAILS if anything functional
could have been lost. A redesign may add views, modifiers and presentation-only @State;
it may never remove behavior. Checked per file (before ⊆ after unless noted):

  ids        every accessibilityIdentifier(...) argument, verbatim
  maestro    every string a .maestro/*.yaml flow matches on, if the file contained it
  state      every @State/@Binding/@Environment/@Bindable/@FocusState/@AppStorage/
             @StateObject/@ObservedObject/@Namespace property name
  funcs      every `func name(` declaration
  calls      every `<receiver>.<member>` reference on app-state receivers
             (store, backend, router, tour, speech, playback, auth, model, vm, session,
             editor, Entitlements.shared, chat, player, camera, AppStore, TourManager)
  hooks      count of each presentation/lifecycle/gesture hook must not drop
             (.sheet, .fullScreenCover, .navigationDestination, .alert, .task,
             .onAppear, .onChange, .onTapGesture, .gesture, .refreshable, .onMove, ...)
  controls   count of interactive controls must not drop, where DS components that
             take an action count as controls (DSRow(…action…), DSChip, DSCircleButton…)

Usage:
  scripts/redesign_guard.py [--base REF] [files...]     # default: all changed .swift files
Exit 0 = PASS, 1 = FAIL.
"""
import argparse, collections, glob, os, re, subprocess, sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                      text=True, check=True).stdout.strip()

ID_RE = re.compile(r'accessibilityIdentifier\((.*?)\)\s*$|accessibilityIdentifier\(("(?:[^"\\]|\\.)*")',
                   re.M)
STATE_RE = re.compile(r'@(State|Binding|Environment|Bindable|FocusState|AppStorage|StateObject|'
                      r'ObservedObject|Namespace|GestureState|SceneStorage)\b[^\n]*?\b(?:var|let)\s+'
                      r'([A-Za-z_][A-Za-z0-9_]*)')
FUNC_RE = re.compile(r'\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)\s*[<(]')
RECEIVERS = (r'store|backend|router|tour|speech|playback|auth|model|vm|viewModel|session|editor|'
             r'chat|player|camera|Entitlements\.shared|AppStore|TourManager|recorder|coordinator|'
             r'purchases|entitlements|pager|engine|timeline')
CALL_RE = re.compile(r'(?<![A-Za-z0-9_.])(?:self\.)?(' + RECEIVERS + r')\.([A-Za-z_][A-Za-z0-9_]*)')
HOOKS = [r'\.sheet\(', r'\.fullScreenCover\(', r'\.navigationDestination\(', r'\.alert\(',
         r'\.confirmationDialog\(', r'\.marqueConfirm\(', r'\.marqueInput\(', r'\.marqueActions\(',
         r'\.popover\(', r'\bNavigationLink\b', r'\.task\b', r'\.onAppear\b', r'\.onDisappear\b',
         r'\.onChange\(', r'\.onReceive\(', r'\.refreshable\b', r'\.onMove\b', r'\.onDelete\b',
         r'\.swipeActions\b', r'\.contextMenu\b', r'\.onTapGesture\b', r'\.gesture\(',
         r'\.simultaneousGesture\(', r'\.highPriorityGesture\(', r'\.onLongPressGesture\b',
         r'\.onSubmit\b', r'\.focused\(', r'\.sensoryFeedback\(', r'\.dropDestination\b',
         r'\.draggable\b', r'\.photosPicker\(', r'\.fileImporter\(', r'\.onOpenURL\b',
         r'\.tourAnchor\(', r'\.searchable\(', r'\.toolbar\b', r'\.interactiveDismissDisabled\b',
         r'\.presentationDetents\(']
CONTROLS = [r'\bButton\s*[({]', r'\bToggle\s*\(', r'\bTextField\s*\(', r'\bSecureField\s*\(',
            r'\bTextEditor\s*\(', r'\bPicker\s*\(', r'\bSlider\s*\(', r'\bDatePicker\s*\(',
            r'\bStepper\s*\(', r'\bPhotosPicker\s*\(', r'\bLink\s*\(', r'\bShareLink\s*\(',
            r'\bMenu\s*[({]', r'\bPrimaryButton\s*\(', r'\bGhostButton\s*\(', r'\bGlassButton\s*\(',
            r'\bDSCircleButton\s*\(', r'\bDSIconButton\s*\(', r'\bDSChip\s*\(', r'\bDSOptionButton\s*\(',
            r'\bDSCheckRow\s*\(', r'\bDSToggleRow\s*\(', r'\bBackCircle\s*\(', r'\bOnbPill\s*\(',
            r'\bMarqueToggle\w*\s*\(', r'\bDSRow\s*\([^)]*action:', r'\bDSEmptyState\s*\([^)]*action:']


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def strip_comments(src):
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'(?m)//.*$', '', src)


def ids(src):
    out = set()
    for m in re.finditer(r'accessibilityIdentifier\(', src):
        i, depth, j = m.end(), 1, m.end()
        while j < len(src) and depth:
            depth += {'(': 1, ')': -1}.get(src[j], 0)
            j += 1
        out.add(re.sub(r'\s+', ' ', src[i:j - 1]).strip())
    return out


def maestro_strings():
    out = set()
    for f in glob.glob(os.path.join(ROOT, '.maestro', '*.yaml')):
        for line in open(f, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line.startswith('#') or 'id:' in line:
                continue
            for key in ('tapOn:', 'assertVisible:', 'assertNotVisible:', 'visible:', 'notVisible:',
                        'text:', 'scrollUntilVisible:'):
                if line.startswith('- ' + key) or line.startswith(key):
                    val = line.split(key, 1)[1].strip().strip('{}').strip()
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    if not val or val.startswith('id'):
                        continue
                    if any(c in val for c in '.*[]()|^$\\'):
                        # regex pattern: guard its literal prefix ("Subscribe for .*/month")
                        prefix = re.split(r'[.*\[\]()|^$\\]', val, 1)[0].strip()
                        if len(prefix) >= 6:
                            out.add(prefix)
                    else:
                        out.add(val)
    return out


def swift_strings(src):
    return set(re.findall(r'"((?:[^"\\]|\\.)*)"', src))


def counts(src, patterns):
    return {p: len(re.findall(p, src)) for p in patterns}


def controls(src):
    return sum(len(re.findall(p, src)) for p in CONTROLS)


def check(path, base, mstrings):
    rel = os.path.relpath(path, ROOT)
    before = git('show', f'{base}:{rel}')
    if before.returncode != 0:
        return rel, [], ['new file (no baseline): reviewed manually']
    b, a = strip_comments(before.stdout), strip_comments(open(path, encoding='utf-8').read())
    fails, warns = [], []

    lost = ids(b) - ids(a)
    if lost:
        fails.append(f'accessibilityIdentifier removed: {sorted(lost)}')

    bs, as_ = swift_strings(b), swift_strings(a)
    for m in sorted(mstrings):
        had = any(m in s for s in bs)
        if had and not any(m in s for s in as_):
            fails.append(f'Maestro-referenced text no longer present: {m!r}')

    lost = set(STATE_RE.findall(b)) - set(STATE_RE.findall(a))
    if lost:
        fails.append(f'state properties removed: {sorted(n for _, n in lost)}')

    lost = set(FUNC_RE.findall(b)) - set(FUNC_RE.findall(a))
    if lost:
        fails.append(f'functions removed: {sorted(lost)}')

    cb = collections.Counter('.'.join(x) for x in CALL_RE.findall(b))
    ca = collections.Counter('.'.join(x) for x in CALL_RE.findall(a))
    gone = sorted(k for k in cb if k not in ca)
    if gone:
        fails.append(f'app-state references removed: {gone}')
    fewer = sorted(f'{k} {cb[k]}->{ca[k]}' for k in cb if k in ca and ca[k] < cb[k])
    if fewer:
        warns.append(f'app-state references fewer (verify intentional dedupe): {fewer}')

    hb, ha = counts(b, HOOKS), counts(a, HOOKS)
    dropped = [f'{p} {hb[p]}->{ha[p]}' for p in HOOKS if ha[p] < hb[p]]
    if dropped:
        fails.append(f'presentation/lifecycle/gesture hooks dropped: {dropped}')

    kb, ka = controls(b), controls(a)
    if ka < kb:
        fails.append(f'interactive controls dropped: {kb} -> {ka}')
    return rel, fails, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=os.environ.get('REDESIGN_BASE', 'redesign-base'))
    ap.add_argument('files', nargs='*')
    args = ap.parse_args()
    files = args.files or [os.path.join(ROOT, f) for f in git('diff', '--name-only', args.base, '--',
                                                               '*.swift').stdout.split() if f]
    files = [f for f in files if f.endswith('.swift') and os.path.exists(f)]
    mstrings = maestro_strings()
    bad = 0
    for f in files:
        rel, fails, warns = check(os.path.abspath(f), args.base, mstrings)
        print(f"{'FAIL' if fails else 'PASS'}  {rel}")
        for x in fails:
            print(f'   ✗ {x}')
        for x in warns:
            print(f'   ! {x}')
        bad += bool(fails)
    print(f'\n{len(files) - bad}/{len(files)} files pass (base {args.base})')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
