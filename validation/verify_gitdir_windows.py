"""Fork-only exact-revision native Windows validation, not an upstream change."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path.cwd()
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
BASE = "42a89d7cc9374a19e72fa57011e5fe3a99c7b3bf"
FIX = "c9eeb93e4f93d7109195904972338f27a6b3a4db"
FILES = ["src/libgit2/config_file.c", "src/libgit2/repository.c",
         "src/libgit2/repository.h", "tests/libgit2/config/conditionals.c",
         "src/util/win32/posix_w32.c", "include/git2/repository.h",
         "include/git2/config.h", "CMakeLists.txt"]
REPORT = {"platform": platform.platform(), "python": sys.version,
          "base": BASE, "fix": FIX, "runs": [], "failures": [],
          "source_hashes": {}, "comparisons": [], "public_paths": []}


def save():
    (OUT / "report.json").write_text(json.dumps(REPORT, indent=2) + "\n")


def check(condition, message):
    if not condition:
        REPORT["failures"].append(message)
    save()


def run(name, args, cwd=ROOT, env=None, timeout=1200):
    args = list(map(str, args))
    start = time.monotonic()
    log = OUT / (name + ".log")
    print(name, " ".join(args), flush=True)
    with log.open("wb") as stream:
        try:
            p = subprocess.run(args, cwd=cwd, env=env, stdout=stream,
                               stderr=subprocess.STDOUT, timeout=timeout, check=False)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    result = {"name": name, "args": args, "cwd": str(cwd), "returncode": code,
              "seconds": time.monotonic() - start, "log": log.name}
    REPORT["runs"].append(result)
    save()
    print(json.dumps(result), flush=True)
    return result


def observe(args, cwd, env):
    p = subprocess.run(list(map(str, args)), cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=30, check=False)
    return {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args])


def build(label, source):
    builddir = source / "build-native"
    args = ["cmake", "-S", source, "-B", builddir, "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Debug", "-DBUILD_SHARED_LIBS=ON", "-DBUILD_TESTS=ON",
            "-DENABLE_WERROR=ON", "-DUSE_HTTPS=Schannel", "-DUSE_SSH=exec",
            "-DUSE_BUNDLED_ZLIB=ON", "-DREGEX_BACKEND=builtin", "-DDEBUG_LEAK_CHECKER=win32"]
    result = run(label + "-configure", args)
    check(result["returncode"] == 0, label + " configure failed")
    if result["returncode"] != 0:
        return None
    result = run(label + "-build", ["cmake", "--build", builddir, "--parallel", "3"])
    check(result["returncode"] == 0, label + " build failed")
    if result["returncode"] != 0:
        return None
    result = run(label + "-conditionals", [builddir / "libgit2_tests.exe", "-v", "-sconfig::conditionals"], builddir)
    check(result["returncode"] == 0, label + " native conditionals failed")
    result = run(label + "-offline-util", ["ctest", "--test-dir", builddir,
                 "--output-on-failure", "--output-junit", OUT / (label + "-ctest.xml"),
                 "-R", "^(offline|util)$"], timeout=1800)
    # Preserve all baseline results. A failed baseline is not a clean-suite pass.
    xml = OUT / (label + "-ctest.xml")
    check(xml.exists(), label + " ctest report missing")
    if xml.exists():
        tests = ET.parse(xml).getroot()
        REPORT[label + "_ctest"] = ET.tostring(tests, encoding="unicode")
    probe = builddir / "gitdir-probe.exe"
    result = run(label + "-probe-build", ["cl", "/nologo", "/W4", "/WX",
                 "/I" + str(source / "include"), ROOT / "driver/validation/gitdir_probe.c",
                 "/Fe:" + str(probe), "/link", "/LIBPATH:" + str(builddir), "git2.lib"], builddir)
    check(result["returncode"] == 0, label + " probe build failed")
    return probe if result["returncode"] == 0 else None


def compare_case(name, config, pattern, cwd, pwd, mode, path, reference, probes, env):
    config.write_text('[core]\nrepositoryformatversion=0\nbare=false\n' +
                      '[includeIf "' + pattern + '"]\npath=included.cfg\n')
    caseenv = dict(env)
    if pwd is None:
        caseenv.pop("PWD", None)
    else:
        caseenv["PWD"] = str(pwd)
    observations = {"git": observe(reference, cwd, caseenv)}
    for label, probe in probes.items():
        observations[label] = observe([probe, mode, path, "probe.value"], cwd, caseenv)
    expected = observations["git"]
    check(expected["exit"] in (0, 1), "Git reference failed: " + name)
    check(expected["stdout"] in ("native-marker\n", ""), "Git reference returned unexpected value: " + name)
    equal = {}
    for label in probes:
        actual = observations[label]
        equal[label] = (actual["exit"], actual["stdout"]) == (expected["exit"], expected["stdout"])
        check(actual["exit"] in (0, 1), label + " probe had an API error: " + name)
        if label == "fix":
            check(equal[label], "Fixed/Git mismatch: " + name)
    REPORT["comparisons"].append({"name": name, "pattern": pattern, "cwd": str(cwd),
                                  "PWD": str(pwd) if pwd is not None else None,
                                  "mode": mode, "path": str(path), "observations": observations,
                                  "equal": equal})
    save()


def main():
    assert sys.platform == "win32", "This driver requires native Windows"
    run("git-version", ["git", "--version"])
    run("cmake-version", ["cmake", "--version"])
    sources = {"base": ROOT / "baseline", "fix": ROOT / "fixed"}
    for label, source in sources.items():
        expected = BASE if label == "base" else FIX
        assert git(source, "rev-parse", "HEAD").decode().strip() == expected
        assert not git(source, "status", "--porcelain", "--untracked-files=no").strip()
        REPORT["source_hashes"][label] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in FILES}
    REPORT["probe_sha256"] = hashlib.sha256((ROOT / "driver/validation/gitdir_probe.c").read_bytes()).hexdigest()
    save()
    probes = {label: build(label, source) for label, source in sources.items()}
    if not all(probes.values()):
        return 1

    fixtures = OUT / "fixtures"
    real = fixtures / "real"
    repo = real / "project"
    home = fixtures / "home"
    repo.mkdir(parents=True)
    home.mkdir()
    env = dict(os.environ)
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "xdg"), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(home / "empty-config"))
    (home / "empty-config").write_text("")
    result = run("fixture-init", ["git", "init", repo], env=env)
    check(result["returncode"] == 0, "Fixture init failed")
    (repo / "subdir").mkdir()
    (repo / ".git/included.cfg").write_text("[probe]\nvalue=native-marker\n")
    links = []
    junction = fixtures / "junction"
    result = run("junction-create", ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(real)])
    check(result["returncode"] == 0 and junction.is_dir(), "Junction fixture unavailable")
    if junction.is_dir():
        links.append(("junction", junction))
    symbolic = fixtures / "symlink"
    try:
        symbolic.symlink_to(real, target_is_directory=True)
        links.append(("symlink", symbolic))
        REPORT["symlink_available"] = True
    except OSError as error:
        REPORT["symlink_available"] = False
        REPORT["symlink_skip_reason"] = str(error)
    save()
    config = repo / ".git/config"
    for kind, link in links:
        alias = link / "project"
        patterns = {
            "alias": "gitdir:" + link.as_posix() + "/",
            "canonical": "gitdir:" + real.as_posix() + "/",
            "wildcard": "gitdir:" + fixtures.as_posix() + "/*/project/.git",
            "casefold": "gitdir/i:" + link.as_posix().upper() + "/",
            "unused": "gitdir:" + (fixtures / "unused").as_posix() + "/",
        }
        for pname, pattern in patterns.items():
            for cwdname, cwd in (("alias", alias), ("real", repo), ("elsewhere", fixtures)):
                for pwdname, pwd in (("missing", None), ("matching", cwd), ("stale", home)):
                    paths = [("alias", alias.as_posix()), ("real", repo.as_posix())]
                    if cwdname != "elsewhere":
                        paths.append(("dot", "."))
                    for pathname, path in paths:
                        name = "-".join((kind, pname, cwdname, pwdname, pathname))
                        compare_case(name, config, pattern, cwd, pwd, "open", path,
                                     ["git", "--git-dir", path + "/.git", "config", "--get", "probe.value"], probes, env)
        # Additional public APIs use the same alias-preservation machinery.
        for pname, pattern in patterns.items():
            compare_case(kind + "-bare-" + pname, config, pattern, fixtures, None,
                         "bare", (alias / ".git").as_posix(),
                         ["git", "--git-dir", (alias / ".git").as_posix(), "config", "--get", "probe.value"], probes, env)
        for pathname, path in (("alias", alias), ("real", repo)):
            observations = {label: observe([probe, "open", path.as_posix(), "--public-path"], fixtures, env)
                            for label, probe in probes.items()}
            check(observations["base"] == observations["fix"], "Public repository path changed: " + kind + pathname)
            check(observations["fix"]["exit"] == 0, "Public repository path lookup failed")
            REPORT["public_paths"].append({"kind": kind, "path": pathname, "observations": observations})

    check(bool(links), "No native alias fixtures were tested")
    REPORT["summary"] = {"cases": len(REPORT["comparisons"]),
                          "mismatches": {label: sum(not c["equal"][label] for c in REPORT["comparisons"])
                                         for label in probes}}
    offline = [r for r in REPORT["runs"] if r["name"].endswith("-offline-util")]
    for result in offline:
        check(result["returncode"] == 0, result["name"] + " did not pass; inspect retained full baseline/fix logs")
    for label, source in sources.items():
        check(not git(source, "diff", "--name-only").strip(), label + " tracked source changed during validation")
    REPORT["finished"] = True
    save()
    return int(bool(REPORT["failures"]))


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        save()
