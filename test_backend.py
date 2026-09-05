#!/usr/bin/env python
"""Test the backend endpoints directly to see error details."""

import sys
from pathlib import Path

# Add backend directory to sys.path so we can import backend modules
backend_path = Path(__file__).resolve().parent / 'backend'
sys.path.insert(0, str(backend_path))

print("Testing positions endpoint directly...")
try:
    from kotak_client import KotakClient
    client = KotakClient()
    print(f"✓ KotakClient created successfully")
    print(f"  Config loaded: {client.config}")
    
    # Now try to get positions
    result = client.get_positions()
    print(f"✓ Positions retrieved: {result}")
except Exception as e:
    print(f"✗ Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

