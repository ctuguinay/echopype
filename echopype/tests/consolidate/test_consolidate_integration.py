import math
import os
import dask
import pathlib
import tempfile

import pytest

import numpy as np
import pandas as pd
import xarray as xr
import scipy.io as io
import echopype as ep
from typing import List

from echopype.consolidate.ek_depth_utils import (
    ek_use_platform_vertical_offsets, ek_use_platform_angles, ek_use_beam_angles
)

"""
For future reference:

For ``test_add_splitbeam_angle`` the test data is in the following locations:
- the EK60 raw file is in `test_data/ek60/DY1801_EK60-D20180211-T164025.raw` and the
associated echoview split-beam data is in `test_data/ek60/splitbeam`.
- the EK80 raw file is in `test_data/ek80_bb_with_calibration/2018115-D20181213-T094600.raw` and
the associated echoview split-beam data is in `test_data/ek80_bb_with_calibration/splitbeam`
"""


@pytest.fixture(
    params=[
        (
            ("EK60", "DY1002_EK60-D20100318-T023008_rep_freq.raw"),
            "EK60",
            None,
            {},
        ),
        (
            ("EK80_NEW", "D20211004-T233354.raw"),
            "EK80",
            None,
            {'waveform_mode': 'CW', 'encode_mode': 'power'},
        ),
        (
            ("AZFP", "17082117.01A"),
            "AZFP",
            ("AZFP", "17041823.XML"),
            {},
        ),
    ],
    ids=[
        "ek60_dup_freq",
        "ek80_cw_power",
        "azfp",
    ],
)
def test_data_samples(request, test_path):
    (
        filepath,
        sonar_model,
        azfp_xml_path,
        range_kwargs,
    ) = request.param
    path_model, *paths = filepath
    filepath = test_path[path_model].joinpath(*paths)

    if azfp_xml_path is not None:
        path_model, *paths = azfp_xml_path
        azfp_xml_path = test_path[path_model].joinpath(*paths)

    return (
        filepath,
        sonar_model,
        azfp_xml_path,
        range_kwargs,
    )


def _check_swap(ds, ds_swap):
    assert "channel" in ds.dims
    assert "frequency_nominal" not in ds.dims
    assert "frequency_nominal" in ds_swap.dims
    assert "channel" not in ds_swap.dims


def test_swap_dims_channel_frequency(test_data_samples):
    """
    Test swapping dimension/coordinate from channel to frequency_nominal.
    """
    (
        filepath,
        sonar_model,
        azfp_xml_path,
        range_kwargs,
    ) = test_data_samples
    ed = ep.open_raw(filepath, sonar_model, azfp_xml_path)
    if ed.sonar_model.lower() == 'azfp':
        avg_temperature = ed['Environment']['temperature'].values.mean()
        env_params = {
            'temperature': avg_temperature,
            'salinity': 27.9,
            'pressure': 59,
        }
        range_kwargs['env_params'] = env_params
        if 'azfp_cal_type' in range_kwargs:
            range_kwargs.pop('azfp_cal_type')

    dup_freq_valueerror = (
        "Duplicated transducer nominal frequencies exist in the file. "
        "Operation is not valid."
    )

    Sv = ep.calibrate.compute_Sv(ed, **range_kwargs)
    try:
        Sv_swapped = ep.consolidate.swap_dims_channel_frequency(Sv)
        _check_swap(Sv, Sv_swapped)
    except Exception as e:
        assert isinstance(e, ValueError) is True
        assert str(e) == dup_freq_valueerror

    MVBS = ep.commongrid.compute_MVBS(Sv)
    try:
        MVBS_swapped = ep.consolidate.swap_dims_channel_frequency(MVBS)
        _check_swap(Sv, MVBS_swapped)
    except Exception as e:
        assert isinstance(e, ValueError) is True
        assert str(e) == dup_freq_valueerror


def _build_ds_Sv(channel, range_sample, ping_time, sample_interval):
    return xr.Dataset(
        data_vars={
            "Sv": (
                ("channel", "range_sample", "ping_time"),
                np.random.random((len(channel), range_sample.size, ping_time.size)),
            ),
            "echo_range": (
                ("channel", "range_sample", "ping_time"),
                (
                    np.swapaxes(np.tile(range_sample, (len(channel), ping_time.size, 1)), 1, 2)
                    * sample_interval
                ),
            ),
        },
        coords={
            "channel": channel,
            "range_sample": range_sample,
            "ping_time": ping_time,
        },
    )


@pytest.mark.unit
@pytest.mark.parametrize("file, sonar_model, compute_Sv_kwaargs", [
    (
        "echopype/test_data/ek60/NBP_B050N-D20180118-T090228.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek60/ncei-wcsd/Summer2017-D20170620-T021537.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH1707/Reduced_D20170826-T205615.raw",
        "EK80",
        {"waveform_mode":"BB", "encode_mode":"complex"}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH2106/EK80/Reduced_Hake-D20210701-T131621.raw",
        "EK80",
        {"waveform_mode":"CW", "encode_mode":"power"}
    )
])
def test_ek_depth_utils_dims(file, sonar_model, compute_Sv_kwaargs):
    """
    Tests `ek_use_platform_vertical_offsets`, `ek_use_platform_angles`, and
    `ek_use_beam_angles` for correct dimensions.
    """
    # Open EK Raw file and Compute Sv
    ed = ep.open_raw(file, sonar_model=sonar_model)
    ds_Sv = ep.calibrate.compute_Sv(ed, **compute_Sv_kwaargs)

    # Check dimensions for using EK platform vertical offsets to compute
    # `transducer_depth`.
    transducer_depth = ek_use_platform_vertical_offsets(
        platform_ds=ed["Platform"], ping_time_da=ds_Sv["ping_time"]
    )
    assert transducer_depth.dims == ('channel', 'ping_time')
    assert transducer_depth["channel"].equals(ds_Sv["channel"])
    assert transducer_depth["ping_time"].equals(ds_Sv["ping_time"])

    # Check dimensions for using EK platform angles to compute
    # `platform_echo_range_scaling`.
    platform_echo_range_scaling = ek_use_platform_angles(
        platform_ds=ed["Platform"], ping_time_da=ds_Sv["ping_time"]
    )
    assert platform_echo_range_scaling.dims == ('ping_time',)
    assert platform_echo_range_scaling["ping_time"].equals(ds_Sv["ping_time"])

    # Check dimensions for using EK beam angles to compute `beam_echo_range_scaling`.
    beam_echo_range_scaling = ek_use_beam_angles(
        beam_ds=ed["Sonar/Beam_group1"], channel_da=ds_Sv["channel"]
    )
    assert beam_echo_range_scaling.dims == ('channel',)
    assert beam_echo_range_scaling["channel"].equals(ds_Sv["channel"])


@pytest.mark.integration
def test_add_depth_without_echodata():
    """
    Test `add_depth` without using Echodata Platform or Beam groups.
    """
    # Build test Sv dataset
    channel = ["channel_0", "channel_1", "channel_2"]
    range_sample = np.arange(100)
    ping_time = pd.date_range(start="2022-08-10T10:00:00", end="2022-08-10T12:00:00", periods=121)
    sample_interval = 0.01
    ds_Sv = _build_ds_Sv(channel, range_sample, ping_time, sample_interval)

    # User input `depth_offset`
    water_level = 10
    ds_Sv_depth = ep.consolidate.add_depth(ds_Sv, depth_offset=water_level)
    assert ds_Sv_depth["depth"].equals(ds_Sv["echo_range"] + water_level)

    # User input `depth_offset` and `tilt`
    tilt = 15
    ds_Sv_depth = ep.consolidate.add_depth(ds_Sv, depth_offset=water_level, tilt=tilt)
    assert ds_Sv_depth["depth"].equals(ds_Sv["echo_range"] * np.cos(tilt / 180 * np.pi) + water_level)

    # Inverted echosounder with `depth_offset` and `tilt`
    ds_Sv_depth = ep.consolidate.add_depth(ds_Sv, depth_offset=water_level, tilt=tilt, downward=False)
    assert ds_Sv_depth["depth"].equals(-1 * ds_Sv["echo_range"] * np.cos(tilt / 180 * np.pi) + water_level)

    # Check history attribute
    history_attribute = ds_Sv_depth["depth"].attrs["history"]
    history_attribute_without_time = history_attribute[33:]
    assert history_attribute_without_time == ". depth` calculated using: Sv `echo_range`."


@pytest.mark.integration
def test_add_depth_errors():
    """Check if all `add_depth` errors are raised appropriately."""
    # Open EK80 Raw file and Compute Sv
    ed = ep.open_raw(
        "echopype/test_data/ek80/ncei-wcsd/SH2106/EK80/Reduced_Hake-D20210701-T131621.raw",
        sonar_model="EK80"
    )
    ds_Sv = ep.calibrate.compute_Sv(ed, **{"waveform_mode":"CW", "encode_mode":"power"})

    # Test that all three errors are called:
    with pytest.raises(ValueError, match=(
        "If any of `use_platform_vertical_offsets` or `use_platform_angles` is `True` "
        + "then `echodata` cannot be `None`."
    )):
        ep.consolidate.add_depth(ds_Sv, None, use_platform_angles=True)
    with pytest.raises(NotImplementedError, match=(
        "Computing depth with both platform and beam angles is not implemented yet."
    )):
        ep.consolidate.add_depth(ds_Sv, ed, use_platform_angles=True, use_beam_angles=True)
    with pytest.raises(NotImplementedError, match=(
        "`use_platform/beam_...` not implemented yet for `AZFP`."
    )):
        ed["Sonar"].attrs["sonar_model"] = "AZFP"
        ep.consolidate.add_depth(ds_Sv, ed, use_platform_angles=True)


@pytest.mark.integration
@pytest.mark.parametrize("file, sonar_model, compute_Sv_kwaargs", [
    (
        "echopype/test_data/ek60/NBP_B050N-D20180118-T090228.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek60/ncei-wcsd/Summer2017-D20170620-T021537.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH1707/Reduced_D20170826-T205615.raw",
        "EK80",
        {"waveform_mode":"BB", "encode_mode":"complex"}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH2106/EK80/Reduced_Hake-D20210701-T131621.raw",
        "EK80",
        {"waveform_mode":"CW", "encode_mode":"power"}
    )
])
def test_add_depth_EK_with_platform_vertical_offsets(file, sonar_model, compute_Sv_kwaargs):
    """Test `depth` values when using EK Platform vertical offset values to compute it."""
    # Open EK Raw file and Compute Sv
    ed = ep.open_raw(file, sonar_model=sonar_model)
    ds_Sv = ep.calibrate.compute_Sv(ed, **compute_Sv_kwaargs)

    # Replace any Platform Vertical Offset NaN values with 0
    ed["Platform"]["water_level"] = ed["Platform"]["water_level"].fillna(0)
    ed["Platform"]["vertical_offset"] = ed["Platform"]["vertical_offset"].fillna(0)
    ed["Platform"]["transducer_offset_z"] = ed["Platform"]["transducer_offset_z"].fillna(0)

    # Compute `depth` using platform vertical offset values
    ds_Sv = ep.consolidate.add_depth(ds_Sv, ed, use_platform_vertical_offsets=True)

    # Check history attribute
    history_attribute = ds_Sv["depth"].attrs["history"]
    history_attribute_without_time = history_attribute[33:]
    assert history_attribute_without_time == (
        ". depth` calculated using: Sv `echo_range`, Echodata Platform Vertical Offset Data."
    )

    # Compute transducer depth
    transducer_depth = ek_use_platform_vertical_offsets(ed["Platform"], ds_Sv["ping_time"])
    
    # Iterate through every channel
    for channel_index in range(len(ds_Sv["channel"])):
        # Iterate through every ping time
        for ping_time_index in range(len(ds_Sv["ping_time"])):
            # Iterate through first 5 range sample indices
            for range_sample_index in range(0, 5):
                # Extract relevant values
                depth_value = ds_Sv["depth"].isel(
                    channel=channel_index, ping_time=ping_time_index, range_sample=range_sample_index
                ).data
                transducer_depth_value = transducer_depth.isel(
                    channel=channel_index, ping_time=ping_time_index
                ).data
                # Check if depth value is equal to corresponding `echo_range` value + transducer depth value
                assert np.isclose(
                    depth_value,
                    ds_Sv["echo_range"].isel(
                        channel=channel_index, ping_time=ping_time_index, range_sample=range_sample_index
                    ).data + transducer_depth_value,
                    rtol=1e-10,
                    atol=1e-10
                )


@pytest.mark.integration
@pytest.mark.parametrize("file, sonar_model, compute_Sv_kwaargs", [
    (
        "echopype/test_data/ek60/NBP_B050N-D20180118-T090228.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek60/ncei-wcsd/Summer2017-D20170620-T021537.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH1707/Reduced_D20170826-T205615.raw",
        "EK80",
        {"waveform_mode":"BB", "encode_mode":"complex"}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH2106/EK80/Reduced_Hake-D20210701-T131621.raw",
        "EK80",
        {"waveform_mode":"CW", "encode_mode":"power"}
    )
])
def test_add_depth_EK_with_platform_angles(file, sonar_model, compute_Sv_kwaargs):
    """Test `depth` values when using EK Platform angles to compute it."""
    # Open EK Raw file and Compute Sv
    ed = ep.open_raw(file, sonar_model=sonar_model)
    ds_Sv = ep.calibrate.compute_Sv(ed, **compute_Sv_kwaargs)

    # Replace any Beam Angle NaN values with 0
    ed["Platform"]["pitch"] = ed["Platform"]["pitch"].fillna(0)
    ed["Platform"]["roll"] = ed["Platform"]["roll"].fillna(0)

    # Compute `depth` using platform angle values
    ds_Sv = ep.consolidate.add_depth(ds_Sv, ed, use_platform_angles=True)

    # Check history attribute
    history_attribute = ds_Sv["depth"].attrs["history"]
    history_attribute_without_time = history_attribute[33:]
    assert history_attribute_without_time == (
        ". depth` calculated using: Sv `echo_range`, Echodata Platform Angle Data."
    )

    # Compute transducer depth
    echo_range_scaling = ek_use_platform_angles(ed["Platform"], ds_Sv["ping_time"])

    # Iterate through every ping time
    for ping_time_index in range(len(ds_Sv["ping_time"])):
        # Grab echo range scaling value
        echo_range_scaling_value = echo_range_scaling.isel(ping_time=ping_time_index)
        # Iterate through every channel
        for channel_index in range(len(ds_Sv["channel"])):
            # Grab vectors
            depth_vector = ds_Sv["depth"].isel(
                channel=channel_index, ping_time=ping_time_index
            )
            echo_range_vector = ds_Sv["echo_range"].isel(
                channel=channel_index, ping_time=ping_time_index
            )
            # Check if depth vector is equal to echo range scaling value * echo range
            assert np.all(
                np.isclose(
                    (depth_vector).data,
                    (echo_range_scaling_value * echo_range_vector).data, 
                    equal_nan=True
                )
            )


@pytest.mark.integration
@pytest.mark.parametrize("file, sonar_model, compute_Sv_kwaargs", [
    (
        "echopype/test_data/ek60/NBP_B050N-D20180118-T090228.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek60/ncei-wcsd/Summer2017-D20170620-T021537.raw",
        "EK60",
        {}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH1707/Reduced_D20170826-T205615.raw",
        "EK80",
        {"waveform_mode":"BB", "encode_mode":"complex"}
    ),
    (
        "echopype/test_data/ek80/ncei-wcsd/SH2106/EK80/Reduced_Hake-D20210701-T131621.raw",
        "EK80",
        {"waveform_mode":"CW", "encode_mode":"power"}
    )
])
def test_add_depth_EK_with_beam_angles(file, sonar_model, compute_Sv_kwaargs):
    """Test `depth` values when using EK Beam angles to compute it."""
    # Open EK Raw file and Compute Sv
    ed = ep.open_raw(file, sonar_model=sonar_model)
    ds_Sv = ep.calibrate.compute_Sv(ed, **compute_Sv_kwaargs)

    # Replace any Beam Angle NaN values with 0
    ed["Sonar/Beam_group1"]["beam_direction_x"] = ed["Sonar/Beam_group1"]["beam_direction_x"].fillna(0)
    ed["Sonar/Beam_group1"]["beam_direction_y"] = ed["Sonar/Beam_group1"]["beam_direction_y"].fillna(0)
    ed["Sonar/Beam_group1"]["beam_direction_z"] = ed["Sonar/Beam_group1"]["beam_direction_z"].fillna(0)

    # Compute `depth` using beam angle values
    ds_Sv = ep.consolidate.add_depth(ds_Sv, ed, use_beam_angles=True)

    # Check history attribute
    history_attribute = ds_Sv["depth"].attrs["history"]
    history_attribute_without_time = history_attribute[33:]
    assert history_attribute_without_time == (
        ". depth` calculated using: Sv `echo_range`, Echodata Beam Angle Data."
    )

    # Compute echo range scaling values
    echo_range_scaling = ek_use_beam_angles(ed["Sonar/Beam_group1"], ds_Sv["channel"])

    # Iterate through every channel
    for channel_index in range(len(ds_Sv["channel"])):
        # Grab echo range scaling value
        echo_range_scaling_value = echo_range_scaling.isel(channel=channel_index)
        # Iterate through every ping time
        for ping_time_index in range(len(ds_Sv["ping_time"])):
            # Grab vectors
            depth_vector = ds_Sv["depth"].isel(
                channel=channel_index, ping_time=ping_time_index
            )
            echo_range_vector = ds_Sv["echo_range"].isel(
                channel=channel_index, ping_time=ping_time_index
            )
            # Check if depth vector is equal to echo range scaling value * echo range
            assert np.all(
                np.isclose(
                    (depth_vector).data,
                    (echo_range_scaling_value * echo_range_vector).data, 
                    equal_nan=True
                )
            )


def _create_array_list_from_echoview_mats(paths_to_echoview_mat: List[pathlib.Path]) -> List[np.ndarray]:
    """
    Opens each mat file in ``paths_to_echoview_mat``, selects the first ``ping_time``,
    and then stores the array in a list.

    Parameters
    ----------
    paths_to_echoview_mat: list of pathlib.Path
        A list of paths corresponding to mat files, where each mat file contains the
        echoview generated angle alongship and athwartship data for a channel

    Returns
    -------
    list of np.ndarray
        A list of numpy arrays generated by choosing the appropriate data from the mat files.
        This list will have the same length as ``paths_to_echoview_mat``
    """

    list_of_mat_arrays = []
    for mat_file in paths_to_echoview_mat:

        # open mat file and grab appropriate data
        list_of_mat_arrays.append(io.loadmat(file_name=mat_file)["P0"]["Data_values"][0][0])

    return list_of_mat_arrays


@pytest.mark.parametrize(
    ["location_type", "sonar_model", "path_model", "raw_and_xml_paths", "extras"],
    [
        (
            "empty-location",
            "EK60",
            "EK60",
            ("ooi/CE02SHBP-MJ01C-07-ZPLSCB101_OOI-D20191201-T000000.raw", None),
            None,
        ),
        (
            "with-track-location",
            "EK60",
            "EK60",
            ("Winter2017-D20170115-T150122.raw", None),
            None,
        ),
        (
            "fixed-location",
            "AZFP",
            "AZFP",
            ("17082117.01A", "17041823.XML"),
            {'longitude': -60.0, 'latitude': 45.0, 'salinity': 27.9, 'pressure': 59},
        ),
    ],
)
def test_add_location(
        location_type,
        sonar_model,
        path_model,
        raw_and_xml_paths,
        extras,
        test_path
):
    # Prepare the Sv dataset
    raw_path = test_path[path_model] / raw_and_xml_paths[0]
    if raw_and_xml_paths[1]:
        xml_path = test_path[path_model] / raw_and_xml_paths[1]
    else:
        xml_path = None

    ed = ep.open_raw(raw_path, xml_path=xml_path, sonar_model=sonar_model)
    if location_type == "fixed-location":
        point_ds = xr.Dataset(
            {
                "latitude": (["time"], np.array([float(extras['latitude'])])),
                "longitude": (["time"], np.array([float(extras['longitude'])])),
            },
            coords={
                "time": (["time"], np.array([ed["Sonar/Beam_group1"]["ping_time"].values.min()]))
            },
        )
        ed.update_platform(point_ds, variable_mappings={"latitude": "latitude", "longitude": "longitude"})

    env_params = None
    # AZFP data require external salinity and pressure
    if sonar_model == "AZFP":
        env_params = {
            "temperature": ed["Environment"]["temperature"].values.mean(),
            "salinity": extras["salinity"],
            "pressure": extras["pressure"],
        }

    ds = ep.calibrate.compute_Sv(echodata=ed, env_params=env_params)

    # add_location tests
    if location_type == "empty-location":
        with pytest.raises(Exception) as exc:
            ep.consolidate.add_location(ds=ds, echodata=ed)
        assert exc.type is ValueError
        assert "Coordinate variables not present or all nan" in str(exc.value)
    else:
        def _tests(ds_test, location_type, nmea_sentence=None):
            # lat,lon & time1 existence
            assert "latitude" in ds_test
            assert "longitude" in ds_test
            assert "time1" not in ds_test

            # lat & lon have a single dimension: 'ping_time'
            assert len(ds_test["longitude"].dims) == 1 and ds_test["longitude"].dims[0] == "ping_time" # noqa
            assert len(ds_test["latitude"].dims) == 1 and ds_test["latitude"].dims[0] == "ping_time" # noqa

            # Check interpolated or broadcast values
            if location_type == "with-track-location":
                for position in ["longitude", "latitude"]:
                    position_var = ed["Platform"][position]
                    if nmea_sentence:
                        position_var = position_var[ed["Platform"]["sentence_type"] == nmea_sentence]
                    position_interp = position_var.interp(time1=ds_test["ping_time"])
                    # interpolated values are identical
                    assert np.allclose(ds_test[position].values, position_interp.values, equal_nan=True) # noqa
            elif location_type == "fixed-location":
                for position in ["longitude", "latitude"]:
                    position_uniq = set(ds_test[position].values)
                    # contains a single repeated value equal to the value passed to update_platform
                    assert (
                            len(position_uniq) == 1 and
                            math.isclose(list(position_uniq)[0], extras[position])
                    )

        ds_all = ep.consolidate.add_location(ds=ds, echodata=ed)
        _tests(ds_all, location_type)

        # the test for nmea_sentence="GGA" is limited to the with-track-location case
        if location_type == "with-track-location":
            ds_sel = ep.consolidate.add_location(ds=ds, echodata=ed, nmea_sentence="GGA")
            _tests(ds_sel, location_type, nmea_sentence="GGA")


@pytest.mark.integration
def test_add_location_time_duplicates_value_error(test_path):   
    """Tests for duplicate time value error in ``add_location``.""" 
    # Open raw and compute the Sv dataset
    raw_path = test_path["EK60"] / "Winter2017-D20170115-T150122.raw"
    ed = ep.open_raw(raw_path, sonar_model="EK60")
    ds = ep.calibrate.compute_Sv(echodata=ed)
    
    # Add duplicates to time1
    ed["Platform"]["time1"].data[0] = ed["Platform"]["time1"].data[1]
    
    # Check if the expected error is logged
    with pytest.raises(ValueError) as exc_info:
        # Run add location with duplicated time
        ep.consolidate.add_location(ds=ds, echodata=ed)

    # Check if the specific error message is in the logs
    assert 'The ``echodata["Platform"]["time1"]`` array contains duplicate values. Downstream interpolation on the position variables requires unique time values.' == str(exc_info.value)


@pytest.mark.integration
def test_add_location_lat_lon_0_NaN_warnings(test_path, caplog):
    """Tests for lat lon 0 and NaN value warnings in ``add_warning``."""
    # Open raw and compute the Sv dataset
    raw_path = test_path["EK60"] / "Winter2017-D20170115-T150122.raw"
    ed = ep.open_raw(raw_path, sonar_model="EK60")
    ds = ep.calibrate.compute_Sv(echodata=ed)
    
    # Add NaN to latitude and 0 to longitude
    ed["Platform"]["latitude"][0] = np.nan
    ed["Platform"]["longitude"][0] = 0

    # Turn on logger verbosity
    ep.utils.log.verbose(override=False)

    # Run add location with 0 and NaN lat/lon values
    ep.consolidate.add_location(ds=ds, echodata=ed)
    
    # Check if the expected warnings are logged
    interp_msg = (
        "Interpolation may be negatively impacted, "
        "consider handling these values before calling ``add_location``."
    )
    expected_warnings = [
        f"Latitude and/or longitude arrays contain NaNs. {interp_msg}",
        f"Latitude and/or longitude arrays contain zeros. {interp_msg}"
    ]
    for warning in expected_warnings:
        assert any(warning in record.message for record in caplog.records)
    
    # Turn off logger verbosity
    ep.utils.log.verbose(override=True)


@pytest.mark.parametrize(
    ("sonar_model", "test_path_key", "raw_file_name", "paths_to_echoview_mat",
     "waveform_mode", "encode_mode", "pulse_compression", "to_disk"),
    [
        # ek60_CW_power
        (
            "EK60", "EK60", "DY1801_EK60-D20180211-T164025.raw",
            [
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T1.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T2.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T3.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T4.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T5.mat'
            ],
            "CW", "power", False, False
        ),
        # ek60_CW_power_Sv_path
        (
            "EK60", "EK60", "DY1801_EK60-D20180211-T164025.raw",
            [
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T1.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T2.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T3.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T4.mat',
                'splitbeam/DY1801_EK60-D20180211-T164025_angles_T5.mat'
            ],
            "CW", "power", False, True
        ),
        # ek80_CW_complex
        (
            "EK80", "EK80_CAL", "2018115-D20181213-T094600.raw",
            [
                'splitbeam/2018115-D20181213-T094600_angles_T1.mat',
                'splitbeam/2018115-D20181213-T094600_angles_T4.mat',
                'splitbeam/2018115-D20181213-T094600_angles_T6.mat',
                'splitbeam/2018115-D20181213-T094600_angles_T5.mat'
            ],
            "CW", "complex", False, False
        ),
        # ek80_BB_complex_no_pc
        (
            "EK80", "EK80_CAL", "2018115-D20181213-T094600.raw",
            [
                'splitbeam/2018115-D20181213-T094600_angles_T3_nopc.mat',
                'splitbeam/2018115-D20181213-T094600_angles_T2_nopc.mat',
            ],
            "BB", "complex", False, False,
        ),
        # ek80_CW_power
        (
            "EK80", "EK80", "Summer2018--D20180905-T033113.raw",
            [
                'splitbeam/Summer2018--D20180905-T033113_angles_T2.mat',
                'splitbeam/Summer2018--D20180905-T033113_angles_T1.mat',
            ],
            "CW", "power", False, False,
        ),
    ],
    ids=[
        "ek60_CW_power",
        "ek60_CW_power_Sv_path",
        "ek80_CW_complex",
        "ek80_BB_complex_no_pc",
        "ek80_CW_power",
    ],
)
def test_add_splitbeam_angle(sonar_model, test_path_key, raw_file_name, test_path,
                             paths_to_echoview_mat, waveform_mode, encode_mode,
                             pulse_compression, to_disk):

    # obtain the EchoData object with the data needed for the calculation
    ed = ep.open_raw(test_path[test_path_key] / raw_file_name, sonar_model=sonar_model)

    # compute Sv as it is required for the split-beam angle calculation
    ds_Sv = ep.calibrate.compute_Sv(ed, waveform_mode=waveform_mode, encode_mode=encode_mode)

    # initialize temporary directory object
    temp_dir = None

    # allows us to test for the case when source_Sv is a path
    if to_disk:

        # create temporary directory for mask_file
        temp_dir = tempfile.TemporaryDirectory()

        # write DataArray to temporary directory
        zarr_path = os.path.join(temp_dir.name, "Sv_data.zarr")
        ds_Sv.to_zarr(zarr_path)

        # assign input to a path
        ds_Sv = zarr_path

    # add the split-beam angles to Sv dataset
    ds_Sv = ep.consolidate.add_splitbeam_angle(source_Sv=ds_Sv, echodata=ed,
                                               waveform_mode=waveform_mode,
                                               encode_mode=encode_mode,
                                               pulse_compression=pulse_compression,
                                               to_disk=to_disk)

    if to_disk:
        assert isinstance(ds_Sv["angle_alongship"].data, dask.array.core.Array)
        assert isinstance(ds_Sv["angle_athwartship"].data, dask.array.core.Array)

    # obtain corresponding echoview output
    full_echoview_path = [test_path[test_path_key] / path for path in paths_to_echoview_mat]
    echoview_arr_list = _create_array_list_from_echoview_mats(full_echoview_path)

    # compare echoview output against computed output for all channels
    for chan_ind in range(len(echoview_arr_list)):

        # grabs the appropriate ds data to compare against
        reduced_angle_alongship = ds_Sv.isel(channel=chan_ind, ping_time=0).angle_alongship.dropna("range_sample")
        reduced_angle_athwartship = ds_Sv.isel(channel=chan_ind, ping_time=0).angle_athwartship.dropna("range_sample")

        # TODO: make "start" below a parameter in the input so that this is not ad-hoc but something known
        # for some files the echoview data is shifted by one index, here we account for that
        if reduced_angle_alongship.shape == (echoview_arr_list[chan_ind].shape[1], ):
            start = 0
        else:
            start = 1

        # note for the checks below:
        #   - angles from CW power data are similar down to 1e-7
        #   - angles computed from complex samples deviates a lot more

        # check the computed angle_alongship values against the echoview output
        assert np.allclose(reduced_angle_alongship.values[start:],
                           echoview_arr_list[chan_ind][0, :], rtol=1e-1, atol=1e-2)

        # check the computed angle_alongship values against the echoview output
        assert np.allclose(reduced_angle_athwartship.values[start:],
                           echoview_arr_list[chan_ind][1, :], rtol=1e-1, atol=1e-2)

    if temp_dir:
        # remove the temporary directory, if it was created
        temp_dir.cleanup()


def test_add_splitbeam_angle_BB_pc(test_path):

    # obtain the EchoData object with the data needed for the calculation
    ed = ep.open_raw(test_path["EK80_CAL"] / "2018115-D20181213-T094600.raw", sonar_model="EK80")

    # compute Sv as it is required for the split-beam angle calculation
    ds_Sv = ep.calibrate.compute_Sv(ed, waveform_mode="BB", encode_mode="complex")

    # add the split-beam angles to Sv dataset
    ds_Sv = ep.consolidate.add_splitbeam_angle(
        source_Sv=ds_Sv, echodata=ed,
        waveform_mode="BB", encode_mode="complex", pulse_compression=True,
        to_disk=False
    )

    # Load pyecholab pickle
    import pickle
    with open(test_path["EK80_EXT"] / "pyecholab/pyel_BB_splitbeam.pickle", 'rb') as handle:
        pyel_BB_p_data = pickle.load(handle)

    # Compare 70kHz channel
    chan_sel = "WBT 714590-15 ES70-7C"

    # Compare cal params
    # dict mappgin:  {pyecholab : echopype}
    cal_params_dict = {
        "angle_sensitivity_alongship": "angle_sensitivity_alongship",
        "angle_sensitivity_athwartship": "angle_sensitivity_athwartship",
        "beam_width_alongship": "beamwidth_alongship",
        "beam_width_athwartship": "beamwidth_athwartship",
    }
    for p_pyel, p_ep in cal_params_dict.items():
        assert np.allclose(pyel_BB_p_data["cal_parms"][p_pyel],
                           ds_Sv[p_ep].sel(channel=chan_sel).values)

    # alongship angle
    pyel_vals = pyel_BB_p_data["alongship_physical"]
    ep_vals = ds_Sv["angle_alongship"].sel(channel=chan_sel).values
    assert pyel_vals.shape == ep_vals.shape
    assert np.allclose(pyel_vals, ep_vals, atol=1e-5)

    # athwartship angle
    pyel_vals = pyel_BB_p_data["athwartship_physical"]
    ep_vals = ds_Sv["angle_athwartship"].sel(channel=chan_sel).values
    assert pyel_vals.shape == ep_vals.shape
    assert np.allclose(pyel_vals, ep_vals, atol=1e-6)


# TODO: need a test for power/angle data, with mock EchoData object
# containing some channels with single-beam data and some channels with split-beam data
