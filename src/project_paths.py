from pathlib import Path
currpath = Path(__file__)
print(currpath)
projroot = currpath.parent.parent
print(projroot)
rawdata = projroot / "data" / "raw"
print(rawdata)
print(rawdata.exists())