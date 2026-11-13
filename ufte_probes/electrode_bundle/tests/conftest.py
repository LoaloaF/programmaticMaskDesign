"""Tests never write into the real output root: point MASKGEN_OUT at a throwaway folder.

Default config paths (BundleConfig.out_dxf, IonpConfig.dxf_file, ...) are built from the maskgen
output root, which is required -- so the suite sets one for itself, for the whole session.
"""
import os
import tempfile

os.environ["MASKGEN_OUT"] = tempfile.mkdtemp(prefix="maskgen_test_out_")
