import pytest
import sys
from dtg1_lookup import LookupTables
from unittest.mock import patch
import builtins

def test_load_from_csv_file_not_found(capsys):
    """
    [ERROR PATH TEST]
    Verifies that a SystemExit is raised when the LUT configuration file
    is not found, preventing the parser from running with an invalid setup.
    Also verifies the printed error message.
    """
    filepath = "nonexistent_path_that_should_never_exist.csv"
    with pytest.raises(SystemExit) as excinfo:
        LookupTables.load_from_csv(filepath)

    assert excinfo.value.code == 1

    # Verify the correct output was printed
    captured = capsys.readouterr()
    assert f"[-] Error: LUT configuration file {filepath} not found." in captured.out

def test_load_from_csv_general_exception(capsys):
    """
    [ERROR PATH TEST]
    Verifies that a SystemExit is raised when an unexpected error occurs
    during loading of the LUT configuration file.
    """
    filepath = "features.csv" # Any valid path that might be accessed

    # We patch open to raise an unexpected Exception
    with patch('builtins.open', side_effect=Exception("Unexpected file access error")):
        with pytest.raises(SystemExit) as excinfo:
            LookupTables.load_from_csv(filepath)

        assert excinfo.value.code == 1

        captured = capsys.readouterr()
        assert f"[-] Critical error parsing {filepath}: Unexpected file access error" in captured.out
