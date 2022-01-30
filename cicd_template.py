r""" Continuous integration by performing tests, coverage, formatting, and package building.

1. if this script runs locally to success, the CI process should also run successfully
2. continuous_integration.py executes automatically in an Azure DevOps Pipeline on push
3. CI results can be viewed after a push at: #TODO add link
4. if CI succeeds in the Azure DevOps pipeline, submit a pull request to complete the continuous deployment build and release

Examples
--------
#### default CI process
python continuous_integration.py
#### using command line arguments specified in conftest.py options
python continuous_integration.py --server=localhost\SQLEXPRESS

See Also
--------
CONTRIBUTING.md CICD Build Pipelines for a general overview of the CICD process
conftest.py options variable for command line arguments allowed
setup.cfg for CICD associated settings
continuous_integration.yml for Azure DevOps Pipeline CI definition
continuous_deployment.py and continuous_deployment.yml for continuous deployment
"""
import os
import subprocess
import configparser
import argparse
import glob
import re

from conftest import options


def run_cmd(cmd, venv=True):
    """Generic command line process and error if needed. Otherwise stdout is returned."""
    # run all commands in virtual environment by default
    if venv:
        cmd[0] = os.path.join(os.getcwd(), "env", "Scripts", cmd[0])
    # call command line process
    status = subprocess.run(cmd, capture_output=True)
    if status.returncode != 0:
        # status = subprocess.call(cmd, shell=True)
        if len(status.stderr) > 0:
            msg = status.stderr.decode("utf-8")
        else:
            msg = status.stdout.decode("utf-8")
        raise RuntimeError(msg)

    return status.stdout.decode("utf-8")


def run_black():
    """Run black to auto-format code to standard."""
    print("running black for all Python files not excluded by .gitignore")
    _ = run_cmd(["black", "."])


def run_flake8(config):
    """Run flake8 to lint and check code quality."""
    try:
        print(
            "running flake8 for all Python files excluding virtual environment directory named 'env'"
        )
        _ = run_cmd(
            [
                "flake8",
                "--exclude=env",
                f"--output-file={config['flake8']['output-file']}",
            ]
        )
        print(f"generated flake8 statistics file: {config['flake8']['output-file']}")
    except RuntimeError:
        print(f"see file for flake8 errors: {config['flake8']['output-file']}")
        raise


def run_coverage_pytest(config, args):
    """Run pytest and coverage to ensure code works as desired and is covered by tests. Also produces test xml report for genbadge."""
    print(f"running coverage for module: {config['metadata']['name']}")
    print(f"running tests for directory: {config['tool:pytest']['testpaths']}")
    # required arguments
    cmd = [
        "coverage",
        "run",
        "--branch",
        "-m",
        f"--source={config['metadata']['name']}",
        "pytest",
        f"--junitxml={config['user:pytest']['junitxml']}",
        "-v",
    ]
    # add optional arguments defined by conftest.py options
    cmd += ["--" + k + "=" + v for k, v in args.items()]

    # use coverage to call pytest
    _ = run_cmd(cmd)
    print(f"generated coverage sqlite file: {config['coverage:run']['data_file']}")
    print(f"generated test xml file: {config['user:pytest']['junitxml']}")


def coverage_html(config):
    """Generage coverage html report for user viewing."""
    print(
        f"generating coverage html file: {os.path.join(config['coverage:html']['directory'], 'index.html')}"
    )
    _ = run_cmd(["coverage", "html"])


def coverage_xml(config):
    """Generate coverage xml report for genbadge."""
    print(f"generating coverage xml file: {config['coverage:xml']['output']}")
    _ = run_cmd(["coverage", "xml"])


def generage_badges(config):
    """Generate badges using genbadge."""
    badges = {
        "tests": config["user:pytest"]["junitxml"],
        "coverage": config["coverage:xml"]["output"],
        "flake8": config["flake8"]["output-file"],
    }
    for b, i in badges.items():
        fp = f"{config['genbadge']['output']}{b}.svg"
        print(f"generating badge for {b} at: {fp}")
        _ = run_cmd(["genbadge", b, "-i", i, "-o", fp])


def package_version():
    "Set package version number in VERSION file using the latest git tag."

    # read latest git tag
    version = run_cmd(["git", "describe", "--tags", "--candidates=1"], venv=False)
    version = version.strip()
    print(f"Latest git tag version: {version}")

    # use clean version
    version = re.search(r"^v(\d+\.\d+\.\d+).*", version)
    if version is None:
        raise RuntimeError("Unable to clean git tag.")
    version = version.group(1)
    with open("VERSION", "w") as fh:
        fh.write(version)
    print(f"Version written to VERSION file: {version}")


def build_package():
    "Build Python package."

    outdir = os.path.join(os.getcwd(), "dist")
    print(f"building package in directory: {outdir}")

    # build package .gz and .whl files
    _ = run_cmd(["python", "-m", "build", f"--outdir={outdir}"])
    print(f"built source archive {glob.glob(os.path.join(outdir,'*.tar.gz'))}")
    print(f"built distribution {glob.glob(os.path.join(outdir,'*.whl'))}")

    # check build result
    _ = run_cmd(["twine", "check", os.path.join(outdir, "*")])


# parameters from setup.cfg
config = configparser.ConfigParser()
config.read("setup.cfg")

# command line arguments from confest options since both pytest and argparse use the same parameters
parser = argparse.ArgumentParser()
for opt in options:
    parser.add_argument(opt, **options[opt])
args = parser.parse_args()

# convert args to dictionary to allow to be used as command line args
args = vars(args)
# ignore None as would be passed as "None"
args = {k: v for k, v in args.items() if v is not None}

# TODO: pre-commit install
run_black()
run_flake8(config)
run_coverage_pytest(config, args)
coverage_html(config)
coverage_xml(config)
generage_badges(config)
package_version()
build_package()
