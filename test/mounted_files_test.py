# Copyright Modal Labs 2022
import os
import pytest
import subprocess
import sys
from pathlib import Path

import pytest_asyncio

import modal
from modal import Mount
from modal.mount import get_auto_mounts

from . import helpers
from .supports.skip import skip_windows


@pytest.fixture
def venv_path(tmp_path, repo_root):
    venv_path = tmp_path
    args = [sys.executable, "-m", "venv", venv_path, "--system-site-packages"]
    if sys.platform == "win32":
        # --copies appears to be broken on Python 3.13.0
        # but I believe it is a no-op on non-windows platforms anyway?
        args.append("--copies")
    subprocess.run(args, check=True)
    # Install Modal and a tiny package in the venv.
    subprocess.run([venv_path / "bin" / "python", "-m", "pip", "install", "-e", repo_root], check=True)
    subprocess.run([venv_path / "bin" / "python", "-m", "pip", "install", "--force-reinstall", "six"], check=True)
    yield venv_path


@pytest.fixture
def path_with_symlinked_files(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("Hello")
    trg = tmp_path / "bar.txt"
    trg.symlink_to(src)
    return tmp_path, {src, trg}


script_path = "pkg_a/script.py"


def f():
    pass


@pytest_asyncio.fixture
async def env_mount_files():
    # If something is installed using pip -e, it will be bundled up as a part of the environment.
    # Those are env-specific so we ignore those as a part of the test
    filenames = []
    for mount in get_auto_mounts():
        async for file_info in mount._get_files(mount.entries):
            filenames.append(file_info.mount_filename)

    return filenames


def test_mounted_files_script(servicer, credentials, supports_dir, env_mount_files, server_url_env):
    print(helpers.deploy_app_externally(servicer, credentials, script_path, cwd=supports_dir))
    files = set(servicer.files_name2sha.keys()) - set(env_mount_files)

    # Assert we include everything from `pkg_a` and `pkg_b` but not `pkg_c`:
    assert files == {
        "/root/a.py",
        "/root/b/c.py",
        "/root/b/e.py",
        "/root/pkg_b/__init__.py",
        "/root/pkg_b/f.py",
        "/root/pkg_b/g/h.py",
        "/root/script.py",
    }


serialized_fn_path = "pkg_a/serialized_fn.py"


def test_mounted_files_serialized(servicer, credentials, supports_dir, env_mount_files, server_url_env):
    helpers.deploy_app_externally(servicer, credentials, serialized_fn_path, cwd=supports_dir)
    files = set(servicer.files_name2sha.keys()) - set(env_mount_files)

    # Assert we include everything from `pkg_a` and `pkg_b` but not `pkg_c`:
    assert files == {
        # should serialized_fn be included? It's not needed to run the function,
        # but it's loaded into sys.modules at definition time...
        "/root/serialized_fn.py",
        # this is mounted under root since it's imported as `import b`
        # and not `import pkg_a.b` from serialized_fn.py
        "/root/b/c.py",
        "/root/b/e.py",  # same as above
        "/root/a.py",  # same as above
        "/root/pkg_b/__init__.py",
        "/root/pkg_b/f.py",
        "/root/pkg_b/g/h.py",
    }


def test_mounted_files_package(supports_dir, env_mount_files, servicer, server_url_env, token_env):
    p = subprocess.run(["modal", "run", "pkg_a.package"], cwd=supports_dir)
    assert p.returncode == 0

    files = set(servicer.files_name2sha.keys()) - set(env_mount_files)
    # Assert we include everything from `pkg_a` and `pkg_b` but not `pkg_c`:
    assert files == {
        "/root/pkg_a/__init__.py",
        "/root/pkg_a/a.py",
        "/root/pkg_a/b/c.py",
        "/root/pkg_a/d.py",
        "/root/pkg_a/b/e.py",
        "/root/pkg_a/script.py",
        "/root/pkg_a/serialized_fn.py",
        "/root/pkg_a/package.py",
        "/root/pkg_b/__init__.py",
        "/root/pkg_b/f.py",
        "/root/pkg_b/g/h.py",
    }


def test_mounted_files_package_no_automount(supports_dir, env_mount_files, servicer, server_url_env, token_env):
    # when triggered like a module, the target module should be put at the correct package path
    p = subprocess.run(
        ["modal", "run", "pkg_a.package"],
        cwd=supports_dir,
        env={**os.environ, "MODAL_AUTOMOUNT": "0"},
    )
    assert p.returncode == 0
    files = set(servicer.files_name2sha.keys()) - set(env_mount_files)
    assert files == {
        "/root/pkg_a/__init__.py",
        "/root/pkg_a/a.py",
        "/root/pkg_a/b/c.py",
        "/root/pkg_a/b/e.py",
        "/root/pkg_a/d.py",
        "/root/pkg_a/package.py",
        "/root/pkg_a/script.py",
        "/root/pkg_a/serialized_fn.py",
    }


@skip_windows("venvs behave differently on Windows.")
def test_mounted_files_sys_prefix(servicer, supports_dir, venv_path, env_mount_files, server_url_env, token_env):
    # Run with venv activated, so it's on sys.prefix, and modal is dev-installed in the VM
    subprocess.run(
        [venv_path / "bin" / "modal", "run", script_path],
        cwd=supports_dir,
    )
    files = set(servicer.files_name2sha.keys()) - set(env_mount_files)
    # Assert we include everything from `pkg_a` and `pkg_b` but not `pkg_c`:
    assert files == {
        "/root/a.py",
        "/root/b/c.py",
        "/root/b/e.py",
        "/root/script.py",
        "/root/pkg_b/__init__.py",
        "/root/pkg_b/f.py",
        "/root/pkg_b/g/h.py",
    }


@pytest.fixture
def symlinked_python_installation_venv_path(tmp_path, repo_root):
    # sets up a symlink to the python *installation* (not just the python binary)
    # and initialize the virtualenv using a path via that symlink
    # This makes the file paths of any stdlib modules use the symlinked path
    # instead of the original, which is similar to what some tools do (e.g. mise)
    # and has the potential to break automounting behavior, so we keep this
    # test as a regression test for that
    venv_path = tmp_path / "venv"
    actual_executable = Path(sys.executable).resolve()
    assert actual_executable.parent.name == "bin"
    python_install_dir = actual_executable.parent.parent
    # create a symlink to the python install *root*
    symlink_python_install = tmp_path / "python-install"
    symlink_python_install.symlink_to(python_install_dir)

    # use a python executable specified via the above symlink
    symlink_python_executable = symlink_python_install / "bin" / actual_executable.name
    # create a new venv
    subprocess.check_call([symlink_python_executable, "-m", "venv", venv_path, "--copies"])
    # check that a builtin module, like ast, is indeed identified to be in the non-resolved install path
    # since this is the source of bugs that we want to assert we don't run into!
    ast_path = subprocess.check_output(
        [venv_path / "bin" / "python", "-c", "import ast; print(ast.__file__);"], encoding="utf8"
    )
    assert ast_path != Path(ast_path).resolve()

    # install modal from current dir
    subprocess.check_call([venv_path / "bin" / "pip", "install", repo_root])
    yield venv_path


@skip_windows("venvs behave differently on Windows.")
def test_mounted_files_symlinked_python_install(
    symlinked_python_installation_venv_path, supports_dir, server_url_env, token_env, servicer
):
    subprocess.check_call(
        [symlinked_python_installation_venv_path / "bin" / "modal", "run", supports_dir / "imports_ast.py"]
    )
    assert "/root/ast.py" not in servicer.files_name2sha


def test_mounted_files_config(servicer, supports_dir, env_mount_files, server_url_env, token_env):
    p = subprocess.run(
        ["modal", "run", "pkg_a/script.py"], cwd=supports_dir, env={**os.environ, "MODAL_AUTOMOUNT": "0"}
    )
    assert p.returncode == 0
    files = set(servicer.files_name2sha.keys()) - set(env_mount_files)
    assert files == {
        "/root/script.py",
    }


def test_e2e_modal_run_py_file_mounts(servicer, credentials, supports_dir):
    helpers.deploy_app_externally(servicer, credentials, "hello.py", cwd=supports_dir)
    # Reactivate the following mount assertions when we remove auto-mounting of dev-installed packages
    # assert len(servicer.files_name2sha) == 1
    # assert servicer.n_mounts == 1  # there should be a single mount
    # assert servicer.n_mount_files == 1
    assert "/root/hello.py" in servicer.files_name2sha


def test_e2e_modal_run_py_module_mounts(servicer, credentials, supports_dir):
    helpers.deploy_app_externally(servicer, credentials, "hello", cwd=supports_dir)
    # Reactivate the following mount assertions when we remove auto-mounting of dev-installed packages
    # assert len(servicer.files_name2sha) == 1
    # assert servicer.n_mounts == 1  # there should be a single mount
    # assert servicer.n_mount_files == 1
    assert "/root/hello.py" in servicer.files_name2sha


def foo():
    pass


def test_mounts_are_not_traversed_on_declaration(supports_dir, monkeypatch, client, server_url_env):
    # TODO: remove once Mount is fully deprecated (replaced by test_image_mounts_are_not_traversed_on_declaration)
    return_values = []
    original = modal.mount._MountDir.get_files_to_upload

    def mock_get_files_to_upload(self):
        r = list(original(self))
        return_values.append(r)
        return r

    monkeypatch.setattr("modal.mount._MountDir.get_files_to_upload", mock_get_files_to_upload)
    app = modal.App()
    mount_with_many_files = Mount._from_local_dir(supports_dir / "pkg_a", remote_path="/test")
    app.function(mounts=[mount_with_many_files])(foo)
    assert len(return_values) == 0  # ensure we don't look at the files yet

    with app.run(client=client):
        pass

    assert return_values  # at this point we should have gotten all the mount files
    # flatten inspected files
    files = set()
    for r in return_values:
        for fn, _ in r:
            files.add(fn)
    # sanity check - this test file should be included since we mounted the test dir
    assert Path(__file__) in files  # this test file should have been included


def test_image_mounts_are_not_traversed_on_declaration(supports_dir, monkeypatch, client, server_url_env):
    return_values = []
    original = modal.mount._MountDir.get_files_to_upload

    def mock_get_files_to_upload(self):
        r = list(original(self))
        return_values.append(r)
        return r

    monkeypatch.setattr("modal.mount._MountDir.get_files_to_upload", mock_get_files_to_upload)
    app = modal.App()
    image_mount_with_many_files = modal.Image.debian_slim().add_local_dir(supports_dir / "pkg_a", remote_path="/test")
    app.function(image=image_mount_with_many_files)(foo)
    assert len(return_values) == 0  # ensure we don't look at the files yet

    with app.run(client=client):
        pass

    assert return_values  # at this point we should have gotten all the mount files
    # flatten inspected files
    files = set()
    for r in return_values:
        for fn, _ in r:
            files.add(fn)
    # sanity check - this test file should be included since we mounted the test dir
    assert Path(__file__) in files  # this test file should have been included


def test_mount_dedupe(servicer, credentials, test_dir, server_url_env):
    supports_dir = test_dir / "supports"
    normally_not_included_file = supports_dir / "pkg_a" / "normally_not_included.pyc"
    normally_not_included_file.touch(exist_ok=True)
    print(
        helpers.deploy_app_externally(
            # no explicit mounts, rely on auto-mounting
            servicer,
            credentials,
            "mount_dedupe.py",
            cwd=test_dir / "supports",
            env={"USE_EXPLICIT": "0"},
        )
    )
    assert servicer.n_mounts == 2
    # the order isn't strictly defined here
    entrypoint_mount, pkg_a_mount = sorted(
        servicer.mounts_excluding_published_client().items(), key=lambda item: len(item[1])
    )
    assert entrypoint_mount[1].keys() == {"/root/mount_dedupe.py"}
    for fn in pkg_a_mount[1].keys():
        assert fn.startswith("/root/pkg_a")
    assert "/root/pkg_a/normally_not_included.pyc" not in pkg_a_mount[1].keys()


def test_mount_dedupe_explicit(servicer, credentials, supports_dir, server_url_env):
    normally_not_included_file = supports_dir / "pkg_a" / "normally_not_included.pyc"
    normally_not_included_file.touch(exist_ok=True)
    print(
        helpers.deploy_app_externally(
            # two explicit mounts of the same package
            servicer,
            credentials,
            "mount_dedupe.py",
            cwd=supports_dir,
            env={"USE_EXPLICIT": "1"},
        )
    )
    assert servicer.n_mounts == 3

    # mounts are loaded in parallel, but there
    mounted_files_sets = {frozenset(m.keys()) for m in servicer.mounts_excluding_published_client().values()}
    assert {"/root/mount_dedupe.py"} in mounted_files_sets
    mounted_files_sets.remove(frozenset({"/root/mount_dedupe.py"}))

    # find one mount that includes normally_not_included.py
    for mount_with_pyc in mounted_files_sets:
        if "/root/pkg_a/normally_not_included.pyc" in mount_with_pyc:
            break
    else:
        assert False, "could not find a mount with normally_not_included.pyc"
    mounted_files_sets.remove(mount_with_pyc)

    # and one without it
    remaining_mount = list(mounted_files_sets)[0]
    assert "/root/pkg_a/normally_not_included.pyc" not in remaining_mount
    for fn in remaining_mount:
        assert fn.startswith("/root/pkg_a")

    assert len(mount_with_pyc) == len(remaining_mount) + 1
    normally_not_included_file.unlink()  # cleanup


def test_mount_dedupe_relative_path_entrypoint(servicer, credentials, supports_dir, server_url_env, monkeypatch):
    workdir = supports_dir / "pkg_a"
    target_app = "../hello.py"  # in parent directory - requiring `..` expansion in path normalization

    helpers.deploy_app_externally(
        # two explicit mounts of the same package
        servicer,
        credentials,
        target_app,
        cwd=workdir,
    )
    # should be only one unique set of files in mounts
    mounted_files_sets = {frozenset(m.keys()) for m in servicer.mounts_excluding_published_client().values()}
    assert len(mounted_files_sets) == 1

    # but there should also be only one actual mount if deduplication works as expected
    assert len(servicer.mounts_excluding_published_client()) == 1


# @skip_windows("pip-installed pdm seems somewhat broken on windows")
# @skip_old_py("some weird issues w/ pdm and Python 3.9", min_version=(3, 10, 0))
@pytest.mark.skip(reason="currently broken on ubuntu github actions")
def test_pdm_cache_automount_exclude(tmp_path, monkeypatch, supports_dir, servicer, server_url_env, token_env):
    # check that `pdm`'s cached packages are not included in automounts
    project_dir = Path(__file__).parent.parent
    monkeypatch.chdir(tmp_path)
    subprocess.run(["pdm", "init", "-n"], check=True)
    subprocess.run(
        ["pdm", "add", "--dev", project_dir], check=True
    )  # install workdir modal into venv, not using cache...
    subprocess.run(["pdm", "config", "--local", "install.cache", "on"], check=True)
    subprocess.run(["pdm", "add", "six"], check=True)  # single file module
    subprocess.run(
        ["pdm", "run", "modal", "deploy", supports_dir / "imports_six.py"], check=True
    )  # deploy a basically empty function

    files = set(servicer.files_name2sha.keys())
    assert files == {
        "/root/imports_six.py",
    }


def test_mount_directory_with_symlinked_file(path_with_symlinked_files, servicer, client):
    path, files = path_with_symlinked_files
    mount = Mount._from_local_dir(path)
    mount._deploy("mo-1", client=client)
    pkg_a_mount = servicer.mount_contents["mo-1"]
    for src_f in files:
        assert any(mnt_f.endswith(src_f.name) for mnt_f in pkg_a_mount)


def test_module_with_dot_prefixed_parent_can_be_mounted(tmp_path, monkeypatch, servicer, client):
    # the typical usecase would be to have a `.venv` directory with a virualenv
    # that could possibly contain local site-packages that a user wants to mount

    # set up some dummy packages:
    # .parent
    #    |---- foo.py
    #    |---- bar
    #    |------|--baz.py
    #    |------|--.hidden_dir
    #    |------|------|-----mod.py
    #    |------|--.hidden_mod.py

    parent_dir = Path(tmp_path) / ".parent"
    parent_dir.mkdir()
    foo_py = parent_dir / "foo.py"
    foo_py.touch()
    bar_package = parent_dir / "bar"
    bar_package.mkdir()
    (bar_package / "__init__.py").touch()
    (bar_package / "baz.py").touch()
    (bar_package / ".hidden_dir").mkdir()
    (bar_package / ".hidden_dir" / "mod.py").touch()  # should be excluded
    (bar_package / ".hidden_mod.py").touch()  # should be excluded

    monkeypatch.syspath_prepend(parent_dir)
    foo_mount = Mount._from_local_python_packages("foo")
    foo_mount._deploy("mo-1", client=client)
    foo_mount_content = servicer.mount_contents["mo-1"]
    assert foo_mount_content.keys() == {"/root/foo.py"}

    bar_mount = Mount._from_local_python_packages("bar")
    bar_mount._deploy("mo-2", client=client)

    bar_mount_content = servicer.mount_contents["mo-2"]
    assert bar_mount_content.keys() == {"/root/bar/__init__.py", "/root/bar/baz.py"}
