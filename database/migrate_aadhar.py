"""
Migration script for Aadhaar hashing and schema updates.
"""
import os
import sys

# Ensure project root is on sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from models.database import Database

def main():
    Database()
    print("Migration complete.")

if __name__ == '__main__':
    main()
