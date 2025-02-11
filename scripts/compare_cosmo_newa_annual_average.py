import logging

import xarray as xr
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib import cm
from matplotlib import colors
import seaborn as sns
import pandas as pd

import geoutil

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S'
)


CMAP = "magma"
CMAP_CORR = "PuOr"
TIMESERIES_COLORS = {
    "newa": "red",
    "cosmo-rea2": "blue",
}


plt.rcParams.update({
    "svg.fonttype": "none"
})


def plot_average_wind_speeds(
    path_to_cosmo_wind_speed, path_to_newa_wind_speed, path_to_cosmo_regridded_wind_speed,
    path_to_cosmo_polys, path_to_newa_polys,
    path_to_ch_shape, height, plot_crs, config, path_to_output
):

    wind_speeds = {
        "cosmo-rea2": xr.open_dataarray(path_to_cosmo_wind_speed),
        "cosmo-rea2-gridded-to-newa": xr.open_dataarray(path_to_cosmo_regridded_wind_speed),
        "newa": xr.open_dataarray(path_to_newa_wind_speed)
    }
    logging.info(f"Loaded wind speed at height {height}")
    polys = {
        "cosmo-rea2": geoutil.load_polygons(path_to_cosmo_polys, config["cosmo-rea2"], plot_crs),
        "cosmo-rea2-gridded-to-newa": geoutil.load_polygons(path_to_newa_polys, config["newa"], plot_crs),
        "newa": geoutil.load_polygons(path_to_newa_polys, config["newa"], plot_crs)
    }
    logging.info("Loaded polygons")

    # ASSUME: we download the GADM file but leave it zipped
    ch_shape = gpd.read_file(f"zip://{path_to_ch_shape}!gadm36_CHE.gpkg", layer=0).to_crs(plot_crs)
    fig = plt.figure(figsize=(15, 10))
    g = plt.GridSpec(
        nrows=5, ncols=6, figure=fig,
        width_ratios=[2, 12, 2, 12, 12, 2], height_ratios=[1, 10, 4, 1, 15],
        wspace=0.02, hspace=0.01
    )
    axes = {}
    polys, min_ws, max_ws = get_per_gridcell_ws(wind_speeds, polys, config)
    logging.info("Loaded average wind speed")
    polys = get_per_gridcell_correlation(wind_speeds, polys)
    logging.info("Loaded correlation")
    annual_ave_ws = get_annual_average_ws(wind_speeds, polys, ch_shape)
    logging.info("Loaded annual wind speed")

    plot_maps(g, axes, polys, config, ch_shape, min_ws, max_ws, height)
    logging.info("Plotted maps")
    plot_timeseries(g, axes, config, annual_ave_ws, height)
    logging.info("Plotted timeseries")
    fig.savefig(path_to_output, pad_inches=0, bbox_inches="tight")


def get_per_gridcell_ws(wind_speeds, polys, config):
    """Get average wind speed per dataset gridcell, across all dataset years"""
    min_ws = 1e10
    max_ws = 0

    for dataset_name, ws in wind_speeds.items():
        _ave_ws = (
            ws
            .mean("time", skipna=True)
            .to_series()
            .reorder_levels([config[dataset_name]["x-name"], config[dataset_name]["y-name"]])
        )
        polys[dataset_name] = polys[dataset_name].assign(ave_ws=_ave_ws)
        min_ws = min(_ave_ws.min(), min_ws)
        max_ws = max(_ave_ws.max(), max_ws)
    return polys, min_ws, max_ws


def get_per_gridcell_correlation(wind_speeds, polys):
    ws_cosmo = (
        wind_speeds["cosmo-rea2-gridded-to-newa"]
        .sel(time=slice("2009", "2013"))
        .reset_coords(drop=True)
    )
    ws_newa = (
        wind_speeds["newa"]
        .sel(time=slice("2009", "2013"))
        .transpose(*ws_cosmo.dims)
        .reset_coords(drop=True)
    )
    # Have to override time dimension as NEWA labels are 15 minutes out of sync with COSMO
    ws_newa.coords["time"] = ws_cosmo.time
    polys["cosmo-rea2-gridded-to-newa"]["corr"] = (
        xr.corr(ws_cosmo.load(), ws_newa.load(), dim="time")
        .to_series()
        .reorder_levels(polys["cosmo-rea2-gridded-to-newa"].index.names)
    )
    return polys


def get_annual_average_ws(wind_speeds, polys, ch_shape):
    """
    Get average annual wind speed across all dataset gridcells in Switzerland
    ASSUME: average of `intersects` and `within` will approximate a geopandas overlay
    (which is a much slower operation, but properly cuts polygons on the border of Switzerland)
    """
    ts = {}
    for dataset in ["cosmo-rea2", "newa"]:
        to_average = []
        for method in ["intersects", "within"]:
            _valid_points = getattr(polys[dataset], method)(ch_shape.geometry[0])
            to_average.append(
                wind_speeds[dataset].where(_valid_points.to_xarray()).groupby("time.year").mean(...)
            )
        ts[dataset] = ((to_average[0] + to_average[1]) / 2).to_series().to_frame("Wind speed")
        # on plotting, only string years render properly
        ts[dataset].index = ts[dataset].index.astype(str).rename("Year")

    return pd.concat(ts.values(), keys=ts.keys(), names=["dataset"]).reset_index()


def plot_maps(g, axes, polys, config, ch_shape, min_ws, max_ws, height):
    axes['map_title'] = plt.subplot(g[0, :], frameon=False)
    axes['map_title'].axis('off')
    axes['map_title'].set_title(
        f"a. {height}m wind speed per gridcell, averaged across all dataset years",
        fontdict={"fontweight": 'bold'}, loc='left', y=0.5
    )
    xlim = [ch_shape.total_bounds[0] * 0.999, ch_shape.total_bounds[2] * 1.001]
    ylim = [ch_shape.total_bounds[1] * 0.999, ch_shape.total_bounds[3] * 1.001]

    for dataset, _ax in {"cosmo-rea2": 0, "cosmo-rea2-gridded-to-newa": 2, "newa": 4}.items():
        axes[f'map_{dataset}'] = plt.subplot(g[1, _ax:_ax + 2], frameon=False)
        axes[f'map_{dataset}'].axis('off')
        axes[f'map_{dataset}'].set_xlim(*xlim)
        axes[f'map_{dataset}'].set_ylim(*ylim)
        axes[f'map_{dataset}_legend'] = plt.subplot(g[2, _ax:_ax + 2], frameon=False)
        if "gridded" in dataset:
            _add_cmap(axes[f'map_{dataset}_legend'], -1, 1, "Hourly wind speed correlation", CMAP_CORR)
            polys[dataset].plot(
                "corr", ax=axes[f'map_{dataset}'], cmap=CMAP_CORR, ec="None", antialiased=False,
                vmin=-1, vmax=1
            )
            title = "Inter-dataset correlation"
        else:
            _add_cmap(axes[f'map_{dataset}_legend'], min_ws, max_ws, "Average wind speed (m/s)", CMAP)
            polys[dataset].plot(
                "ave_ws", ax=axes[f'map_{dataset}'], cmap=CMAP, ec="None", antialiased=False,
                vmin=min_ws, vmax=max_ws
            )
            title = config[dataset]["long-name"]
        axes[f'map_{dataset}'].annotate(
            title, (0.5, 1.01), ha="center", va="bottom",
            xycoords='axes fraction', size=12, fontstyle="italic", c="black"
        )
        ch_shape.plot(ax=axes[f'map_{dataset}'], ec="white", fc="None")
        #plot_license = "Switzerland outline: © 2018 GADM"
        #axes[f'map_{dataset}'].annotate(
        #    plot_license, (0, 0), xycoords='axes fraction', size=12, c="black", va="bottom",
        #    bbox={"facecolor": "white", "alpha": 0.5, "pad": 0, "ec": "None"}
        #)


def _add_cmap(axis, _min, _max, label, cmap):
    axis.axis("off")
    cbar = plt.colorbar(
        cm.ScalarMappable(norm=colors.Normalize(_min, _max), cmap=cmap),
        ax=axis, orientation="horizontal", fraction=1, anchor=(0.5, 1),
        pad=0, aspect=40, shrink=0.8
    )
    cbar.set_label(label, size=12)


def plot_timeseries(g, axes, config, annual_ave_ws, height):
    axes['timeseries_title'] = plt.subplot(g[3, :], frameon=False)
    axes['timeseries_title'].axis('off')
    axes['timeseries_title'].set_title(
        f"b. {height}m wind speed per year, averaged across all gridcells in Switzerland",
        fontweight='bold', loc='left', y=0.5
    )
    axes['timeseries'] = plt.subplot(g[4, 1:])

    sns.lineplot(
        data=annual_ave_ws, x="Year", y="Wind speed", hue="dataset", marker="o",
        palette=TIMESERIES_COLORS, ax=axes['timeseries']
    )

    axes['timeseries'].set_ylabel("Annual average wind speed (m/s)", size=12)
    axes['timeseries'].legend(
        labels=[config[dataset]["long-name"] for dataset in annual_ave_ws.dataset.unique()],
        frameon=False
    )
    sns.despine(ax=axes['timeseries'])


if __name__ == "__main__":
    plot_average_wind_speeds(
        path_to_cosmo_wind_speed=snakemake.input.cosmo_wind_speed,
        path_to_newa_wind_speed=snakemake.input.newa_wind_speed,
        path_to_cosmo_regridded_wind_speed=snakemake.input.cosmo_regridded_wind_speed,
        path_to_cosmo_polys=snakemake.input.cosmo_polys,
        path_to_newa_polys=snakemake.input.newa_polys,
        path_to_ch_shape=snakemake.input.ch_shape,
        config=snakemake.params.config,
        height=snakemake.wildcards.height,
        plot_crs=snakemake.params.plot_crs,
        path_to_output=snakemake.output[0]
    )
