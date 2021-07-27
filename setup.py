from setuptools import find_packages, setup

# PEP0440 compatible formatted version, see:
# https://www.python.org/dev/peps/pep-0440/
#
# release markers:
#   X.Y
#   X.Y.Z   # For bugfix releases
#
# pre-release markers:
#   X.YaN   # Alpha release
#   X.YbN   # Beta release
#   X.YrcN  # Release Candidate
#   X.Y     # Final release

setup(
    name="cached-path",
    version="0.1.0",
    # description="An open-source NLP research library, built on PyTorch.",
    # long_description=open("README.md").read(),
    # long_description_content_type="text/markdown",
    classifiers=[
        "Intended Audience :: Science/Research",
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    # keywords="allennlp NLP deep learning machine reading",
    # url="https://github.com/allenai/allennlp",
    author="Allen Institute for Artificial Intelligence",
    author_email="allennlp@allenai.org",
    license="Apache",
    packages=find_packages(
        exclude=[
            "*.tests",
            "*.tests.*",
            "tests.*",
            "tests",
            "test_fixtures",
            "test_fixtures.*",
            "benchmarks",
            "benchmarks.*",
        ]
    ),
    install_requires=[
        "overrides==3.1.0",
        "boto3>=1.14,<2.0",
        "botocore",
        "requests>=2.18",
        "tqdm>=4.19",
        "filelock>=3.0,<3.1",
        "huggingface_hub>=0.0.8",
        "google-cloud-storage>=1.38.0,<1.42.0",
    ],
    entry_points={"console_scripts": ["cached-path=cached_path:main"]},
    include_package_data=True,
    python_requires=">=3.6.1",
    zip_safe=False,
)
