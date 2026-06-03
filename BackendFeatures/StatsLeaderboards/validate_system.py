#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Comprehensive System Validation Script
Validates the complete authentication and leaderboard system
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple

class SystemValidator:
    """Comprehensive system validation"""
    
    def __init__(self):
        """Initialize validator"""
        self.project_root = Path(__file__).parent
        self.errors = []
        self.warnings = []
        self.passed_checks = []
        self._skip_dirs = ["cdk.out", "layers", "testing"]
        self._max_depth = 3  # Validate up to 3 levels deep

    def _should_skip(self, path):
        """Check if a path should be skipped based on directories to skip"""
        path_parts = path.parts
        for skip_dir in self._skip_dirs:
            if skip_dir in path_parts:
                return True
        return False

    def _get_depth(self, path):
        """Get directory depth relative to project root"""
        return len(path.relative_to(self.project_root).parts) - 1

    def _collect_python_files(self):
        """Collect Python files respecting skip and depth constraints"""
        python_files = []
        for py_file in self.project_root.glob("**/*.py"):
            if py_file.name.startswith('.'):
                continue
            if self._should_skip(py_file):
                continue
            if self._get_depth(py_file) > self._max_depth:
                continue
            python_files.append(py_file)
        return python_files

    def validate_all(self) -> bool:
        """Run all validation checks"""
        print("\n")
        print("=" * 60)
        print("🔍 Starting Comprehensive System Validation...")
        print("=" * 60)
        
        # File structure validation
        self._validate_file_structure()
        
        # Code syntax validation
        self._validate_python_syntax()
        
        # Code integrity validation (corruption detection)
        self._validate_code_integrity()
        
        # Lambda Layers validation
        self._validate_lambda_layers()
        
        # CDK configuration validation
        self._validate_cdk_config()
        
        # Documentation validation
        self._validate_documentation()
        
        # Function import validation
        self._validate_function_imports()
        
        # Print results
        self._print_results()
        
        return len(self.errors) == 0
    
    def _validate_file_structure(self):
        """Validate required files and directories exist"""
        print("\n📁 Validating File Structure...")
        
        required_files = [
            # Core infrastructure
            "app.py",
            "app_post_deploy.py",
            "cdk.json",
            "requirements.txt",
            
            # Authentication system
            "auth/backendAuthorizer.py",
            "auth/playerAuthorizer.py",
            "backend/developerRegistration.py",
            
            # Backend functions
            "backend/leaderboardsConfig.py",
            "backend/resetLeaderboard.py",
            "backend/rebuildLeaderboard.py",
            "backend/batchStoreStatsAndScores.py",
            
            # Player functions
            "player/storePlayerStatsAndScores.py",
            "player/getPlayerStatsAndScores.py",
            "player/getLeaderboardScores.py",
            "player/getPlayerLBStanding.py",
            
            # Lambda Layers
            "layers/valkey-glide-layer/requirements.txt",
            "layers/build_layer.sh",
            "layers/README.md",
        ]
        
        for file_path in required_files:
            full_path = self.project_root / file_path
            if full_path.exists():
                self.passed_checks.append(f"✅ {file_path} exists")
            else:
                self.errors.append(f"❌ Missing required file: {file_path}")
        
        # Optional files — warn if missing but don't block deployment
        optional_files = [
            "studio_parameters.json",
        ]
        
        for file_path in optional_files:
            full_path = self.project_root / file_path
            if full_path.exists():
                self.passed_checks.append(f"✅ {file_path} exists")
            else:
                self.warnings.append(f"⚠️ Optional file missing: {file_path} (deploy.sh will prompt you to create one)")
        
        # Check for required directories
        required_dirs = ["auth", "backend", "player", "testing", "layers"]
        for dir_path in required_dirs:
            full_path = self.project_root / dir_path
            if full_path.is_dir():
                self.passed_checks.append(f"✅ Directory {dir_path}/ exists")
            else:
                self.errors.append(f"❌ Missing required directory: {dir_path}/")
    
    def _validate_python_syntax(self):
        """Validate Python syntax in all Python files with comprehensive checks"""
        print("\n🐍 Validating Python Syntax...")
        
        python_files = self._collect_python_files()
        
        syntax_errors = []
        import_errors = []
        encoding_errors = []
        
        for py_file in python_files:
            relative_path = py_file.relative_to(self.project_root)
            
            try:
                # Check file encoding and readability
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Check for empty files
                if not content.strip():
                    self.warnings.append(f"⚠️ Empty Python file: {relative_path}")
                    continue
                
                # Check for basic syntax errors by compiling
                try:
                    compile(content, str(py_file), 'exec')
                    self.passed_checks.append(f"✅ {relative_path} syntax OK")
                except SyntaxError as e:
                    error_msg = f"❌ Syntax error in {relative_path}:"
                    error_msg += f"\n   Line {e.lineno}: {e.text.strip() if e.text else 'N/A'}"
                    error_msg += f"\n   Error: {e.msg}"
                    syntax_errors.append(error_msg)
                    continue
                
                # Check for import statement validity
                try:
                    import ast
                    tree = ast.parse(content, filename=str(py_file))
                    
                    # Validate import statements
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.Import, ast.ImportFrom)):
                            # Check for common import issues
                            if isinstance(node, ast.ImportFrom):
                                if node.module and '.' in str(node.module):
                                    # Check for relative imports that might be problematic
                                    if node.level > 0 and not any(name.name for name in node.names):
                                        self.warnings.append(f"⚠️ Potential relative import issue in {relative_path} line {node.lineno}")
                    
                    self.passed_checks.append(f"✅ {relative_path} AST validation OK")
                    
                except SyntaxError as e:
                    import_errors.append(f"❌ AST parsing error in {relative_path}: {e}")
                
                # Check for indentation consistency
                lines = content.split('\n')
                indent_types = set()
                for line_num, line in enumerate(lines, 1):
                    if line.strip() and line.startswith((' ', '\t')):
                        if line.startswith('\t'):
                            indent_types.add('tab')
                        elif line.startswith(' '):
                            indent_types.add('space')
                
                if len(indent_types) > 1:
                    self.warnings.append(f"⚠️ {relative_path} mixes tabs and spaces for indentation")
                
                # Check for function/class definition syntax
                try:
                    tree = ast.parse(content)
                    
                    # Build a set of nodes that are inside except handlers (import fallback stubs)
                    except_handler_children = set()
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ExceptHandler):
                            for child in ast.walk(node):
                                except_handler_children.add(id(child))
                    
                    for node in ast.walk(tree):
                        if isinstance(node, ast.FunctionDef):
                            # Check for functions with no body (skip import fallback stubs)
                            if not node.body or (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                                if id(node) in except_handler_children:
                                    continue  # Intentional fallback stub
                                if not any(isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant) 
                                         and isinstance(child.value.value, str) for child in node.body):
                                    self.warnings.append(f"⚠️ {relative_path} has empty function '{node.name}' at line {node.lineno}")
                        
                        elif isinstance(node, ast.ClassDef):
                            # Check for classes with no body (skip import fallback stubs)
                            if not node.body or (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                                if id(node) in except_handler_children:
                                    continue  # Intentional fallback stub
                                self.warnings.append(f"⚠️ {relative_path} has empty class '{node.name}' at line {node.lineno}")
                
                except Exception as e:
                    # Don't fail validation for AST analysis issues
                    pass
                
            except UnicodeDecodeError as e:
                encoding_errors.append(f"❌ Encoding error in {relative_path}: {e}")
            except FileNotFoundError:
                self.errors.append(f"❌ File not found: {relative_path}")
            except PermissionError:
                self.errors.append(f"❌ Permission denied reading: {relative_path}")
            except Exception as e:
                self.warnings.append(f"⚠️ Could not validate {relative_path}: {e}")
        
        # Add all collected errors to main error list
        self.errors.extend(syntax_errors)
        self.errors.extend(import_errors)
        self.errors.extend(encoding_errors)
        
        # Summary of syntax validation
        total_files = len(python_files)
        if syntax_errors or import_errors or encoding_errors:
            print(f"   Found issues in {len(syntax_errors + import_errors + encoding_errors)} of {total_files} Python files")
        else:
            print(f"   All {total_files} Python files passed syntax validation")
            self.passed_checks.append(f"✅ All {total_files} Python files have valid syntax")
            
        # Add information about skipped directories
        print(f"   Skipped directories: {', '.join(self._skip_dirs)}")
        print(f"   Maximum directory depth: {self._max_depth}")
        self.passed_checks.append(f"✅ Optimized validation - skipped {', '.join(self._skip_dirs)} directories")
    
    def _validate_cdk_config(self):
        """Validate CDK configuration"""
        print("\n☁️ Validating CDK Configuration...")
        
        # Check cdk.json
        cdk_json = self.project_root / "cdk.json"
        if cdk_json.exists():
            try:
                with open(cdk_json, 'r') as f:
                    cdk_config = json.load(f)
                
                required_keys = ["app", "watch", "context"]
                for key in required_keys:
                    if key in cdk_config:
                        self.passed_checks.append(f"✅ cdk.json has required key: {key}")
                    else:
                        self.warnings.append(f"⚠️ cdk.json missing recommended key: {key}")
                        
            except json.JSONDecodeError as e:
                self.errors.append(f"❌ Invalid JSON in cdk.json: {e}")
        
        # Check requirements.txt
        requirements_file = self.project_root / "requirements.txt"
        if requirements_file.exists():
            try:
                with open(requirements_file, 'r') as f:
                    requirements = f.read()
                
                required_packages = [
                    "aws-cdk-lib",
                    "constructs",
                    "boto3",
                    "aws-lambda-powertools"
                ]
                
                for package in required_packages:
                    if package in requirements:
                        self.passed_checks.append(f"✅ requirements.txt includes {package}")
                    else:
                        self.warnings.append(f"⚠️ requirements.txt missing {package}")
                        
            except Exception as e:
                self.errors.append(f"❌ Could not read requirements.txt: {e}")
    
    def _validate_code_integrity(self):
        """Validate code integrity and detect corruption patterns"""
        print("\n🔍 Validating Code Integrity...")
        
        python_files = self._collect_python_files()
        
        any_corruption_found = False
        
        for py_file in python_files:
            relative_path = py_file.relative_to(self.project_root)
            file_corrupted = False
            
            try:
                # Read file as binary to detect corruption
                with open(py_file, 'rb') as f:
                    raw_content = f.read()
                
                # Check file size
                if len(raw_content) == 0:
                    self.warnings.append(f"⚠️ Empty file: {relative_path}")
                    continue
                
                # Check for binary corruption patterns
                corruption_checks = [
                    # Null bytes (should not be in Python source)
                    (b'\x00', "null bytes - possible binary corruption"),
                    
                    # UTF-16/UTF-32 BOMs in what should be UTF-8
                    (b'\xff\xfe', "UTF-16 LE BOM - file may be saved in wrong encoding"),
                    (b'\xfe\xff', "UTF-16 BE BOM - file may be saved in wrong encoding"),
                    (b'\xff\xfe\x00\x00', "UTF-32 LE BOM - file may be saved in wrong encoding"),
                    (b'\x00\x00\xfe\xff', "UTF-32 BE BOM - file may be saved in wrong encoding"),
                    
                    # Control characters that shouldn't be in source code
                    (b'\x01', "SOH control character"),
                    (b'\x02', "STX control character"),
                    (b'\x03', "ETX control character"),
                    (b'\x04', "EOT control character"),
                    (b'\x05', "ENQ control character"),
                    (b'\x06', "ACK control character"),
                    (b'\x07', "BEL control character"),
                    (b'\x0e', "SO control character"),
                    (b'\x0f', "SI control character"),
                    
                    # Common file corruption signatures
                    (b'\x89PNG', "PNG image header in Python file"),
                    (b'GIF8', "GIF image header in Python file"),
                    (b'\xff\xd8\xff', "JPEG image header in Python file"),
                    (b'PK\x03\x04', "ZIP file header in Python file"),
                    (b'%PDF', "PDF file header in Python file"),
                ]
                
                for pattern, description in corruption_checks:
                    if pattern in raw_content:
                        if pattern == b'\xef\xbb\xbf':  # UTF-8 BOM is acceptable
                            self.warnings.append(f"⚠️ {relative_path} contains UTF-8 BOM")
                        else:
                            # Check if this pattern is in a string literal (avoid false positives)
                            try:
                                text_content_for_check = raw_content.decode('utf-8', errors='ignore')
                                pattern_str = pattern.decode('utf-8', errors='ignore')
                                
                                # If the pattern appears to be in string literals, skip it
                                if (f'"{pattern_str}"' in text_content_for_check or 
                                    f"'{pattern_str}'" in text_content_for_check or
                                    f'b"{pattern.decode("latin-1", errors="ignore")}"' in text_content_for_check or
                                    f"b'{pattern.decode('latin-1', errors='ignore')}'" in text_content_for_check):
                                    continue  # Skip false positives from string literals
                                
                                # This appears to be real corruption
                                self.errors.append(f"❌ {relative_path} contains {description}")
                                file_corrupted = True
                            except:
                                # If we can't decode, it's likely real corruption
                                self.errors.append(f"❌ {relative_path} contains {description}")
                                file_corrupted = True
                
                # Check for reasonable file size (Python files shouldn't be extremely large)
                if len(raw_content) > 1024 * 1024:  # 1MB
                    self.warnings.append(f"⚠️ {relative_path} is unusually large ({len(raw_content)} bytes)")
                
                # Try to decode as UTF-8 and check for replacement characters
                try:
                    text_content = raw_content.decode('utf-8')
                    if '\ufffd' in text_content:  # Unicode replacement character
                        self.errors.append(f"❌ {relative_path} contains Unicode replacement characters - possible encoding corruption")
                        file_corrupted = True
                    
                    # Check for mixed line endings
                    if '\r\n' in text_content and '\n' in text_content.replace('\r\n', ''):
                        self.warnings.append(f"⚠️ {relative_path} has mixed line endings")
                    
                    # Check for extremely long lines (possible corruption)
                    lines = text_content.split('\n')
                    for line_num, line in enumerate(lines, 1):
                        if len(line) > 1000:  # Very long line
                            self.warnings.append(f"⚠️ {relative_path} line {line_num} is extremely long ({len(line)} chars)")
                    
                    # Check for suspicious character sequences (but avoid false positives from code)
                    suspicious_patterns = [
                        ('<<<<<<< HEAD', 'Git merge conflict marker'),
                        ('>>>>>>> ', 'Git merge conflict marker'),
                        ('=======\n', 'Git merge conflict marker'),  # Only match if it's on its own line
                        ('\x00', 'Null character in text'),
                    ]
                    
                    for pattern, description in suspicious_patterns:
                        if pattern in text_content:
                            # Avoid false positives by checking context
                            lines = text_content.split('\n')
                            for line_num, line in enumerate(lines, 1):
                                if pattern in line:
                                    # Skip if it's in a string literal or comment
                                    stripped_line = line.strip()
                                    if (stripped_line.startswith('#') or  # Comment
                                        stripped_line.startswith('"""') or  # Docstring
                                        stripped_line.startswith("'''") or  # Docstring
                                        ('"""' in stripped_line and stripped_line.count('"""') >= 2) or  # Inline docstring
                                        ("'''" in stripped_line and stripped_line.count("'''") >= 2) or  # Inline docstring
                                        ('"' in stripped_line and pattern in stripped_line.split('"')[1::2]) or  # In double quotes
                                        ("'" in stripped_line and pattern in stripped_line.split("'")[1::2])):  # In single quotes
                                        continue  # Skip false positives
                                    
                                    # This looks like a real issue
                                    self.errors.append(f"❌ {relative_path} line {line_num} contains {description}")
                                    file_corrupted = True
                                    break
                
                except UnicodeDecodeError as e:
                    self.errors.append(f"❌ {relative_path} has encoding corruption: {e}")
                    file_corrupted = True
                
                if file_corrupted:
                    any_corruption_found = True
                else:
                    self.passed_checks.append(f"✅ {relative_path} integrity OK")
                
            except Exception as e:
                self.warnings.append(f"⚠️ Could not check integrity of {relative_path}: {e}")
        
        if not any_corruption_found:
            total_files = len(python_files)
            self.passed_checks.append(f"✅ All {total_files} Python files passed integrity checks")
            
        # Add information about skipped directories
        print(f"   Skipped directories: {', '.join(self._skip_dirs)}")
        print(f"   Maximum directory depth: {self._max_depth}")
        print(f"   Validated {len(python_files)} Python files for code integrity")
        self.passed_checks.append(f"✅ Optimized integrity validation - skipped {', '.join(self._skip_dirs)} directories")

    def _validate_lambda_layers(self):
        """Validate Lambda Layers configuration and structure"""
        print("\n🏗️ Validating Lambda Layers...")
        
        # Check layers directory exists
        layers_dir = self.project_root / "layers"
        if not layers_dir.exists():
            self.errors.append("❌ Missing layers/ directory - Lambda Layers are required for deployment")
            return
        
        self.passed_checks.append("✅ layers/ directory exists")
        
        # Check valkey-glide-layer directory
        valkey_layer_dir = layers_dir / "valkey-glide-layer"
        if not valkey_layer_dir.exists():
            self.errors.append("❌ Missing layers/valkey-glide-layer/ directory")
            return
        
        self.passed_checks.append("✅ valkey-glide-layer directory exists")
        
        # Check layer requirements.txt
        layer_requirements = valkey_layer_dir / "requirements.txt"
        if layer_requirements.exists():
            try:
                with open(layer_requirements, 'r') as f:
                    requirements_content = f.read()
                
                # Check for essential dependencies
                required_packages = [
                    "valkey-glide",
                    "aws-lambda-powertools",
                    "pydantic"
                ]
                
                missing_packages = []
                for package in required_packages:
                    if package not in requirements_content:
                        missing_packages.append(package)
                
                if missing_packages:
                    self.errors.append(f"❌ Layer requirements.txt missing packages: {', '.join(missing_packages)}")
                else:
                    self.passed_checks.append("✅ Layer requirements.txt includes all essential packages")
                    
            except Exception as e:
                self.errors.append(f"❌ Could not read layer requirements.txt: {e}")
        else:
            self.errors.append("❌ Missing layers/valkey-glide-layer/requirements.txt")
        
        # Check for build script
        build_script = layers_dir / "build_layer.sh"
        if build_script.exists():
            if os.access(build_script, os.X_OK):
                self.passed_checks.append("✅ Layer build script exists and is executable")
            else:
                self.warnings.append("⚠️ Layer build script exists but is not executable")
        else:
            self.warnings.append("⚠️ Layer build script (build_layer.sh) not found")
        
        # Check if layer has been built (python directory exists)
        python_dir = valkey_layer_dir / "python"
        if python_dir.exists():
            # Check if it has content
            try:
                contents = list(python_dir.iterdir())
                if contents:
                    self.passed_checks.append("✅ Layer has been built (python/ directory with content)")
                    
                    # Check for key packages in built layer
                    key_packages = ["valkey_glide", "aws_lambda_powertools", "pydantic"]
                    found_packages = []
                    for item in contents:
                        if item.is_dir():
                            package_name = item.name.replace('-', '_')
                            if any(pkg in package_name for pkg in key_packages):
                                found_packages.append(package_name)
                    
                    if found_packages:
                        self.passed_checks.append(f"✅ Layer contains key packages: {', '.join(found_packages)}")
                    else:
                        self.warnings.append("⚠️ Layer built but key packages not found in python/ directory")
                else:
                    self.warnings.append("⚠️ Layer python/ directory exists but is empty - run build_layer.sh")
            except Exception as e:
                self.warnings.append(f"⚠️ Could not check layer contents: {e}")
        else:
            self.warnings.append("⚠️ Layer not built yet - run layers/build_layer.sh before deployment")
        
        # Check layer README
        layer_readme = layers_dir / "README.md"
        if layer_readme.exists():
            self.passed_checks.append("✅ Layer documentation (README.md) exists")
        else:
            self.warnings.append("⚠️ Layer README.md not found")
        
        # Validate app.py references layers
        app_py = self.project_root / "app.py"
        if app_py.exists():
            try:
                with open(app_py, 'r') as f:
                    app_content = f.read()
                
                # Check for layer creation method
                if "_create_shared_layer" in app_content:
                    self.passed_checks.append("✅ app.py includes layer creation method")
                else:
                    self.errors.append("❌ app.py missing _create_shared_layer method")
                
                # Check for layer usage in functions
#               if '"layers": [shared_layer]' in app_content or "'layers': [shared_layer]" in app_content:
                if "layers=[shared_layer]" in app_content:
                    self.passed_checks.append("✅ app.py configures functions to use shared layer")
                else:
                    self.errors.append("❌ app.py functions not configured to use shared layer")
                
                # Check for LayerVersion import
                if "lambda_.LayerVersion" in app_content:
                    self.passed_checks.append("✅ app.py imports LayerVersion from CDK")
                else:
                    self.errors.append("❌ app.py missing LayerVersion import")
                    
            except Exception as e:
                self.warnings.append(f"⚠️ Could not validate app.py layer configuration: {e}")

    def _validate_documentation(self):
        """Validate documentation completeness"""
        print("\n📚 Validating Documentation...")
        
        # Check main readme exists and is readable
        readme_path = self.project_root / "readme.md"
        if readme_path.exists():
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    readme_content = f.read()
                
                if len(readme_content.strip()) > 0:
                    self.passed_checks.append("✅ readme.md exists and has content")
                else:
                    self.warnings.append("⚠️ readme.md is empty")
                        
            except Exception as e:
                self.errors.append(f"❌ Error reading readme.md: {e}")
        else:
            self.warnings.append("⚠️ readme.md not found")
    
    def _validate_function_imports(self):
        """Validate that all Lambda functions can import required modules"""
        print("\n📦 Validating Function Dependencies...")
        
        # This would require actually trying to import the modules
        # For now, we'll do basic checks
        
        lambda_functions = [
            "auth/backendAuthorizer.py",
            "auth/playerAuthorizer.py",
            "backend/developerRegistration.py",
            "backend/leaderboardsConfig.py",
            "backend/resetLeaderboard.py",
            "backend/rebuildLeaderboard.py",
            "backend/batchStoreStatsAndScores.py",
            "player/storePlayerStatsAndScores.py",
            "player/getPlayerStatsAndScores.py",
            "player/getLeaderboardScores.py",
            "player/getPlayerLBStanding.py",
        ]
        
        for func_path in lambda_functions:
            func_file = self.project_root / func_path
            if func_file.exists():
                try:
                    with open(func_file, 'r') as f:
                        content = f.read()
                    
                    # Check for common imports
                    if "import boto3" in content:
                        self.passed_checks.append(f"✅ {func_path} imports boto3")
                    
                    if "aws_lambda_powertools" in content:
                        self.passed_checks.append(f"✅ {func_path} uses PowerTools")
                        
                except Exception as e:
                    self.warnings.append(f"⚠️ Could not validate imports in {func_path}: {e}")
    
    def _print_results(self):
        """Print validation results with detailed breakdown"""
        print("\n" + "=" * 60)
        print("📊 VALIDATION RESULTS")
        print("=" * 60)
        
        # Categorize results for better reporting
        syntax_checks = [c for c in self.passed_checks if 'syntax OK' in c or 'AST validation OK' in c]
        integrity_checks = [c for c in self.passed_checks if 'integrity OK' in c]
        layer_checks = [c for c in self.passed_checks if 'Layer' in c or 'layer' in c]
        file_checks = [c for c in self.passed_checks if 'exists' in c and 'Layer' not in c]
        config_checks = [c for c in self.passed_checks if any(x in c for x in ['cdk.json', 'requirements.txt', 'JSON']) and 'Layer' not in c]
        other_checks = [c for c in self.passed_checks if c not in syntax_checks + integrity_checks + layer_checks + file_checks + config_checks]
        
        syntax_errors = [e for e in self.errors if 'Syntax error' in e or 'AST parsing error' in e]
        corruption_errors = [e for e in self.errors if any(x in e for x in ['corruption', 'null bytes', 'BOM', 'control character', 'encoding corruption', 'merge conflict'])]
        other_errors = [e for e in self.errors if e not in syntax_errors + corruption_errors]
        
        print(f"\n✅ PASSED CHECKS ({len(self.passed_checks)}):")
        
        if file_checks:
            print(f"  📁 File Structure: {len(file_checks)} files/directories found")
        
        if syntax_checks:
            print(f"  🐍 Python Syntax: {len(syntax_checks)} files validated")
        
        if integrity_checks:
            print(f"  🔍 Code Integrity: {len(integrity_checks)} files checked")
        
        if layer_checks:
            print(f"  🏗️ Lambda Layers: {len(layer_checks)} layer validations")
        
        if config_checks:
            print(f"  ⚙️ Configuration: {len(config_checks)} config items validated")
        
        if other_checks:
            print(f"  ✨ Other Checks: {len(other_checks)} additional validations")
        
        # Show first few specific checks
        for check in self.passed_checks[:10]:
            print(f"  {check}")
        if len(self.passed_checks) > 10:
            print(f"  ... and {len(self.passed_checks) - 10} more")
        
        if self.warnings:
            print(f"\n⚠️ WARNINGS ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"  {warning}")
        
        if self.errors:
            print(f"\n❌ ERRORS ({len(self.errors)}):")
            
            if syntax_errors:
                print(f"  🐍 Python Syntax Errors ({len(syntax_errors)}):")
                for error in syntax_errors:
                    print(f"    {error}")
            
            if corruption_errors:
                print(f"  🔍 Code Corruption/Integrity Errors ({len(corruption_errors)}):")
                for error in corruption_errors:
                    print(f"    {error}")
            
            if other_errors:
                print(f"  📋 Other Errors ({len(other_errors)}):")
                for error in other_errors:
                    print(f"    {error}")
        
        print("\n" + "=" * 60)
        
        if self.errors:
            print("❌ VALIDATION FAILED")
            print(f"   {len(self.errors)} errors must be fixed before deployment")
            if syntax_errors:
                print(f"   - {len(syntax_errors)} Python syntax errors")
            if corruption_errors:
                print(f"   - {len(corruption_errors)} code corruption/integrity issues")
            if other_errors:
                print(f"   - {len(other_errors)} other configuration errors")
        elif self.warnings:
            print("⚠️ VALIDATION PASSED WITH WARNINGS")
            print(f"   {len(self.warnings)} warnings should be addressed")
            print("   System can be deployed but warnings should be reviewed")
        else:
            print("✅ VALIDATION PASSED")
            print("   System is ready for deployment!")
        
        print("=" * 60)

def main():
    """Main validation function"""
    validator = SystemValidator()
    success = validator.validate_all()
    
    if success:
        print("\n🚀 Next Steps:")
        print("1. Deploy the system: ./deploy.sh")
        print("2. Run tests: cd testing && python test_StatsAndLeaderboards.py")
        print("3. Register your first game studio via the API")
        sys.exit(0)
    else:
        print("\n🛠️ Please fix the errors above before proceeding.")
        sys.exit(1)

if __name__ == "__main__":
    main()
