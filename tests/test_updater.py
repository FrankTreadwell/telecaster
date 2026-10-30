from telecaster.updater import (Release, cleanup, install, install_destination, is_prerelease, parse_version,
                                pick_update)

EXE = "-windows-x64.exe"


def release(tag, prerelease=False, draft=False, assets=("windows-x64.exe", "macos-arm64.zip")):
    version = tag.lstrip("v")
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [
            {
                "name": f"Telecaster-{version}-{a}",
                "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/Telecaster-{version}-{a}",
                "size": 1234,
                "digest": "sha256:" + "AB" * 32,
            }
            for a in assets
        ],
    }


def test_version_order():
    order = ["0.1.0", "0.2.0a1", "0.2.0b1", "0.2.0b2", "0.2.0rc1", "0.2.0", "0.2.1", "0.10.0", "1.0"]
    keys = [parse_version(v) for v in order]
    assert keys == sorted(keys)
    assert parse_version("v0.2.0b2") == parse_version("0.2.0b2")
    assert parse_version("nightly") is None
    assert is_prerelease("0.2.0b2") and not is_prerelease("0.2.0")


def test_picks_newest_with_a_download():
    releases = [release("v0.2.0b3", True), release("v0.2.0b4", True, assets=("macos-arm64.zip",)),
                release("v0.2.0b1", True)]
    found = pick_update(releases, "0.2.0b2", EXE)
    assert found.version == "0.2.0b3"
    assert found.asset_name == "Telecaster-0.2.0b3-windows-x64.exe"
    assert found.size == 1234 and found.sha256 == "ab" * 32


def test_up_to_date_and_drafts():
    assert pick_update([release("v0.2.0b2", True)], "0.2.0b2", EXE) is None
    assert pick_update([release("v0.3.0", draft=True)], "0.2.0b2", EXE) is None
    assert pick_update("not a list", "0.2.0b2", EXE) is None


def test_betas_only_offered_to_beta_users():
    releases = [release("v0.3.0b1", True), release("v0.2.1")]
    assert pick_update(releases, "0.2.0", EXE).version == "0.2.1"
    assert pick_update(releases, "0.2.0b2", EXE).version == "0.3.0b1"


def test_source_installs_ignore_assets():
    found = pick_update([release("v0.2.0b3", True, assets=())], "0.2.0b2", None)
    assert found.version == "0.2.0b3" and not found.asset_url


def test_swap_running_exe(tmp_path):
    exe = tmp_path / "Telecaster.exe"
    exe.write_bytes(b"old")
    download = tmp_path / "Telecaster.exe.download"
    download.write_bytes(b"new")
    install(exe, download)
    assert exe.read_bytes() == b"new"
    assert (tmp_path / "Telecaster.exe.old").read_bytes() == b"old"
    assert not download.exists()
    cleanup(exe)
    assert not (tmp_path / "Telecaster.exe.old").exists()
    assert exe.read_bytes() == b"new"


def test_versioned_exe_takes_the_new_versions_name(tmp_path):
    exe = tmp_path / "Telecaster-0.2.0b3-windows-x64.exe"
    exe.write_bytes(b"old")
    download = tmp_path / (exe.name + ".download")
    download.write_bytes(b"new")
    new = Release("0.2.0b4", "page", "Telecaster-0.2.0b4-windows-x64.exe", "url")
    dest = install_destination(exe, new, current="0.2.0b3")
    assert dest == tmp_path / "Telecaster-0.2.0b4-windows-x64.exe"
    install(exe, download, dest)
    assert dest.read_bytes() == b"new"
    assert not exe.exists() and not download.exists()
    cleanup(dest)
    assert sorted(p.name for p in tmp_path.iterdir()) == [dest.name]


def test_renamed_exe_keeps_its_name(tmp_path):
    new = Release("0.2.0b4", "page", "Telecaster-0.2.0b4-windows-x64.exe", "url")
    exe = tmp_path / "Telecaster.exe"
    assert install_destination(exe, new, current="0.2.0b3") == exe
