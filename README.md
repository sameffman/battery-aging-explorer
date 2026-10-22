# Battery Aging Explorer

Interactive views of capacity fade and impedance in the KIT NMC/C-SiO aging dataset (228 cells), with the Python script that prepares the data. Live site: https://sameffman.github.io/battery-aging-explorer/

## Data Attribution and Licensing
The data is obtained from: Luh, M. & Blank, T. Comprehensive battery aging dataset: capacity and impedance fade measurements of a lithium-ion NMC/C-SiO cell [dataset – version 2: result data]. RADAR4KIT https://doi.org/10.35097/1969 (2024).

The dataset above updates the initial dataset: Luh, M. & Blank, T. Comprehensive battery aging dataset: capacity and impedance fade measurements of a lithium-ion NMC/C-SiO cell [dataset]. RADAR4KIT https://doi.org/10.35097/1947 (2024).

The dataset is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode).

The dataset's measurement setup, data processing and variable definitions are described in: 
Luh, M., Blank, T. Comprehensive battery aging dataset: capacity and impedance fade measurements of a lithium-ion NMC/C-SiO cell. Sci Data 11, 1004 (2024). https://doi.org/10.1038/s41597-024-03831-x.

The data has been modified: the values shown are derived from the dataset by this project's processing. Any errors in that processing are not the dataset authors'.

## How Values are Derived

The preprocessing script (scripts/preprocess.py) searches the raw data (data/raw, not included in repo due to size) and identifies 228 cells by the condition and replicate in each file name, matching each cell to its data files. It then writes conditions.json, capacity.json, eis_r.json and impedance/<cell>.json to data/processed/.

### Capacity Fade
Capacity values were used as derived by the authors. The authors used the cells' 3.0 Ah nominal capacity as a reference point to examine capacity fade, and cycled the cells to 40% of nominal capacity at standard cycling or 50% nominal capacity for check-up cycles, with some failing before reaching those thresholds.

For this project, the capacity fade was measured against the initial capacity measurement of the cells in addition to their rated 3.0 Ah value. The point at which measured capacity faded to 80% relative to both the measured initial capacity and the rated 3.0 Ah was recorded due to its usage as an end-of-life metric for Li-ion batteries. This value was found by linear interpolation of measured capacity fade values above and below 80%. Some cells did not fall below 80%.

### Impedance
The impedance spectra were used as stored in the dataset, using the authors' compensated impedance values. Spectra marked invalid, and points with missing values, were dropped. At each check-up, a spectrum is measured at five states of charge (10, 30, 50, 70 and 90%), at room temperature and at the cell's aging (operating) temperature.

Two resistances were calculated from each spectrum in this project, following the definitions in the authors' paper and example scripts (https://github.com/energystatusdata/bat-age-data-scripts):
- **R0** is the real part of the impedance where the spectrum crosses the real axis, found by linear interpolation between the two neighboring points, searched between 200 Hz and 3.3 kHz.
- **R1** is the real part of the impedance at the knee of the spectrum, minus R0. The knee is the point of lowest phase angle among the local minima of the phase at or below 33 Hz.

The authors take the lowest phase angle over a fixed frequency range (0.49 to 33 Hz) without requiring a local minimum, which can return an end point of the range instead of a knee. Here, the knee must be a local minimum of the phase at or below 33 Hz, with at least two measured points at lower frequencies. A spectrum with no such knee has no R1. The authors' own R0 and R1 values are in their `LOG_AGE` files, averaged over each check-up; those files are not used here, and the values in this project are calculated for each spectrum individually.

R0 and R1 are read directly from the measured spectra and are not fitted circuit parameters. A small number of spectra have no R0, and ~3% have no R1; those entries are left empty.

## Running Locally

### Viewing HTML Pages
Open a local Python server within the repository folder by using the command: `python3 -m http.server`. Then, view the pages in a browser at http://localhost:8000/. An internet connection is needed as the pages load Plotly from a CDN.

### Recreating Processed Data Files
The raw data can be downloaded from version 2 of the dataset (https://doi.org/10.35097/1969). It will download as a TAR file. In the unpacked folder, the raw data is located in data/dataset. Rename the dataset folder to "raw", place it in the data folder in the local repository, and run `python3 scripts/preprocess.py` to output the processed JSON files. 