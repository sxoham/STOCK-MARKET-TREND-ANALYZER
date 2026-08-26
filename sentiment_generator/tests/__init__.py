import os
import sys

os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
torch_lib_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'torch', 'lib')
if os.path.exists(torch_lib_dir):
    os.environ['PATH'] = torch_lib_dir + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(torch_lib_dir)
        except Exception:
            pass

try:
    import torch
except Exception:
    pass
