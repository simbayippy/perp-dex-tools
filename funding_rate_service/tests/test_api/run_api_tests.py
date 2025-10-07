#!/usr/bin/env python3
"""
Run API Tests

Runs all API endpoint tests with pytest.
"""

import sys
import subprocess
from pathlib import Path


def main():
    """Run API tests"""
    print("=" * 70)
    print("🧪 Running API Endpoint Tests")
    print("=" * 70)
    print()
    
    # Get the funding_rate_service directory
    service_dir = Path(__file__).parent.parent
    
    # Run pytest on test_api directory
    test_dir = service_dir / "tests" / "test_api"
    
    if not test_dir.exists():
        print(f"❌ Test directory not found: {test_dir}")
        return 1
    
    print(f"📂 Test directory: {test_dir}")
    print()
    
    # Build pytest command
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_dir),
        "-v",                    # Verbose
        "--tb=short",            # Short traceback
        "--color=yes",           # Colored output
        "-s",                    # Show print statements
        "--durations=10",        # Show 10 slowest tests
    ]
    
    # Add coverage if available
    try:
        import pytest_cov
        cmd.extend([
            "--cov=api",
            "--cov-report=term-missing",
            "--cov-report=html:htmlcov_api"
        ])
        print("📊 Coverage enabled (output: htmlcov_api/)")
    except ImportError:
        print("ℹ️  Coverage not available (install pytest-cov)")
    
    print()
    print("Running tests...")
    print("-" * 70)
    print()
    
    # Run tests
    result = subprocess.run(cmd, cwd=service_dir)
    
    print()
    print("-" * 70)
    
    if result.returncode == 0:
        print("✅ All API tests passed!")
    else:
        print(f"❌ Tests failed with exit code {result.returncode}")
    
    print("=" * 70)
    
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

