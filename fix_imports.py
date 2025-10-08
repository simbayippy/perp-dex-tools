#!/usr/bin/env python3
"""
Fix all relative imports in funding_rate_service to use absolute imports.
"""

import os
import re
from pathlib import Path

def fix_imports_in_file(file_path: Path):
    """Fix imports in a single file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # Fix imports that start with these modules
        modules_to_fix = [
            'database', 'models', 'core', 'utils', 'api', 
            'collection', 'tasks', 'scripts'
        ]
        
        for module in modules_to_fix:
            # Pattern: from module.something import ...
            pattern = rf'^from {module}\.'
            replacement = f'from funding_rate_service.{module}.'
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        
        # Only write if content changed
        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✅ Fixed imports in: {file_path}")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ Error fixing {file_path}: {e}")
        return False

def main():
    """Fix all import issues in funding_rate_service."""
    
    funding_service_dir = Path("funding_rate_service")
    
    if not funding_service_dir.exists():
        print("❌ funding_rate_service directory not found")
        return
    
    print("🔧 Fixing all relative imports in funding_rate_service...")
    
    # Find all Python files
    python_files = list(funding_service_dir.rglob("*.py"))
    
    fixed_count = 0
    total_count = len(python_files)
    
    for py_file in python_files:
        # Skip __pycache__ and other non-source files
        if "__pycache__" in str(py_file) or py_file.name.startswith('.'):
            continue
            
        if fix_imports_in_file(py_file):
            fixed_count += 1
    
    print(f"\n📊 Summary:")
    print(f"   Total Python files: {total_count}")
    print(f"   Files with fixes: {fixed_count}")
    print(f"   Files unchanged: {total_count - fixed_count}")
    
    if fixed_count > 0:
        print(f"\n🎉 Fixed {fixed_count} files! Try running the bot now.")
    else:
        print(f"\n✅ No import fixes needed.")

if __name__ == "__main__":
    main()
