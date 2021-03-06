"""
Functions used to integrate data from multiple loom files

Below code was written/developed by Fangming Xie, Ethan Armand, and Wayne Doyle

(C) 2019 Mukamel Lab GPLv2
"""

import loompy
import pandas as pd
import numpy as np
from scipy import sparse
import logging
from . import utils

# Start log
int_log = logging.getLogger(__name__)


def high_mem_get_data(loom_file,
                      layer,
                      feat_attr,
                      cell_attr,
                      valid_ra,
                      valid_ca,
                      remove_version,
                      verbose):
    """
    Gets relevant counts and type information for a given loom file

    Args:
        loom_file (str): Path to loom file
        layer (str): Layer in loom_file containing counts
        feat_attr (str): Row attribute containing unique feature IDs
        cell_attr (str): Column attribute containing unique cell IDs
        valid_ra (str/None): Row attribute specifying rows to include
        valid_ca (str/None): Column attribute specifying columns to include
        remove_version (bool): If True, remove GENCODE version ID
        verbose (bool): If true, print logging messages
    """
    if verbose:
        int_log.info('Obtaining counts from layer {0} in {1}'.format(layer, loom_file))
    # Get indices
    row_idx = utils.get_attr_index(loom_file=loom_file,
                                   attr=valid_ra,
                                   columns=False,
                                   as_bool=False,
                                   inverse=False)
    col_idx = utils.get_attr_index(loom_file=loom_file,
                                   attr=valid_ca,
                                   columns=True,
                                   as_bool=False,
                                   inverse=False)
    # Get data
    with loompy.connect(loom_file) as ds:
        dat = ds.layers[layer].sparse(row_idx, col_idx).todense()
        dat = pd.DataFrame(dat,
                           index=ds.ra[feat_attr][row_idx],
                           columns=ds.ca[cell_attr][col_idx])
    # Process data
    if remove_version:
        dat.index = utils.remove_gene_version(dat.index.values)
    dat = dat.T
    return dat


def low_mem_add_data(in_loom,
                     out_loom,
                     layer,
                     feat_attr,
                     cell_attr,
                     label=None,
                     valid_ra=None,
                     valid_ca=None,
                     remove_version=False,
                     batch_size=5000,
                     gen_out=False,
                     verbose=False):
    """
    Gets relevant counts and type information for a given loom file

    Args:
        in_loom (str): Path to loom file containing imputed counts
        out_loom (str): Path to output loom file
        layer (str): Layer in loom_file containing counts
        feat_attr (str): Row attribute containing unique feature IDs
        cell_attr (str): Column attribute containing unique cell IDs
        label (str): Label to add for in_loom
            Added as column attribute Modality
        valid_ra (str/None): Row attribute specifying rows to include
        valid_ca (str/None): Column attribute specifying columns to include
        remove_version (bool): If True, remove GENCODE version ID
        batch_size (int): Number of cells to load per batch
        gen_out (bool): If true, generate out_loom
        verbose (bool): If true, print logging messages
    """
    if verbose:
        int_log.info('Adding counts from layer {0} in {1}'.format(layer, in_loom))
    # Handle inputs
    row_idx = utils.get_attr_index(loom_file=in_loom,
                                   attr=valid_ra,
                                   columns=False,
                                   as_bool=False,
                                   inverse=False)
    col_idx = utils.get_attr_index(loom_file=in_loom,
                                   attr=valid_ca,
                                   columns=True,
                                   as_bool=False,
                                   inverse=False)
    layers = utils.make_layer_list(layer)
    append = True
    if gen_out:
        append = False
        with loompy.connect(in_loom, mode='r') as ds:
            out_ids = ds.ra['Accession'][row_idx]
    else:
        with loompy.connect(out_loom, mode='r') as ds:
            out_ids = ds.ra['Accession']
    if remove_version:
        out_ids = utils.remove_gene_version(out_ids)
    # Get data
    with loompy.connect(in_loom) as ds:
        # Get feature information
        feat_ids = ds.ra[feat_attr][row_idx]
        if remove_version:
            feat_ids = utils.remove_gene_version(feat_ids)
        # Re-order if appending
        feat_idx = pd.DataFrame({'old_idx': np.arange(feat_ids.shape[0])},
                                index=feat_ids)
        feat_idx = feat_idx.loc[out_ids]
        feat_idx['new_idx'] = np.arange(feat_idx.shape[0])
        feat_idx = pd.Series(feat_idx['new_idx'].values,
                             index=feat_idx['old_idx'].values)
        feat_idx = feat_idx.to_dict()
        # Loop over file
        for (_, selection, view) in ds.scan(axis=1,
                                            items=col_idx,
                                            layers=layers,
                                            batch_size=batch_size):
            dat = view.layers[layer].sparse(row_idx, np.arange(view.shape[1]))
            # Update order if appending to match previous data
            if append:
                orig_idx = pd.Series(dat.nonzero()[0])
                new_idx = orig_idx.replace(to_replace=feat_idx)
                dat = sparse.coo_matrix((dat.data,
                                         (new_idx.values, dat.nonzero()[1])),
                                        (dat.shape[0], dat.shape[1]))
                add_ids = out_ids.copy()
            else:
                add_ids = feat_ids.copy()
            # Get column information
            col_attrs = {'CellID': view.ca[cell_attr],
                         'OriginalFile': np.repeat(in_loom, view.shape[1])}
            if label is not None:
                col_attrs['Modality'] = np.repeat(label, view.shape[1])
            # Add to loom file
            utils.batch_add_sparse(loom_file=out_loom,
                                   layers={'': dat},
                                   row_attrs={'Accession': add_ids},
                                   col_attrs=col_attrs,
                                   append=append,
                                   empty_base=False,
                                   batch_size=batch_size)
            append = True


def high_mem_repeat_label(loom_file,
                          valid_ca,
                          label):
    """
    Makes an array with a label repeated multiple times

    Args:
        loom_file (str): Path to loom file
        valid_ca (str): Column attribute in loom_file specifying valid cells
        label (str): Label to add

    Returns:
        labels (array): Label repeated sum(valid_ca) times
    """
    col_idx = utils.get_attr_index(loom_file=loom_file,
                                   attr=valid_ca,
                                   columns=True,
                                   as_bool=False,
                                   inverse=False)
    labels = np.repeat(label, col_idx.shape[0])
    return labels


def high_mem_integrate(loom_source,
                       loom_target,
                       loom_output,
                       layer_source='',
                       layer_target='',
                       feat_source='Accession',
                       feat_target='Accession',
                       cell_source='CellID',
                       cell_target='CellID',
                       label_source=None,
                       label_target=None,
                       valid_ra_source=None,
                       valid_ra_target=None,
                       valid_ca_source=None,
                       valid_ca_target=None,
                       remove_version=False,
                       verbose=False):
    """
    Generates an integrated loom file containing observed and imputed counts for a single modality
        Is fast, but uses a lot of memory

    Args:
        loom_source (str): Path to source loom file (modality that loom_target is imputed into)
        loom_target (str/list): Path(s) to target files (modality/modalities that receive imputed counts)
        loom_output (str): Path to output loom file that contains observed/imputed counts for a given modality
        layer_source (str): Layer in loom_source containing observed counts
        layer_target (str/list): Layer(s) in loom_target containing imputed counts
        feat_source (str): Row attribute containing unique feature IDs in loom_source
            IDs will be included in loom_output under the row attribute Accession
        feat_target (str/list): Row attribute(s) containing unique feature IDs in loom_target
        cell_source (str): Column attribute containing unique cell IDs in loom_source
            IDs will be included in loom_output under the column attribute CellID
        cell_target (str/list): Column attribute(s) containing unique cell IDs in loom_target
        label_source (str/None): Optional, labels to be added to cells from loom_source
            Will be saved in the column attribute Modality
            If provided, label_target must also be provided
        label_target (str/list/None): Optional, labels to be added to cells from loom_target
        valid_ra_source (str/None): Optional, row attribute specifying features to include in loom_source
        valid_ra_target (str/list/None): Optional, row attribute specifying features to include in loom_target
        valid_ca_source (str/None): Optional, column attribute specifying cells to include in loom_source
        valid_ca_target (str/list/None): Optional, column attribute specifying cells to include in loom_target
        remove_version (bool): If true, remove GENCODE version ID
        verbose (bool): If true, print logging messages
    """
    # Check inputs
    is_type = False
    if label_source is not None and label_target is not None:
        is_type = True
    is_a_list = False
    if isinstance(loom_target, list):
        utils.all_same_type_size(parameters=[loom_target,
                                             layer_target],
                                 expected_type='list',
                                 confirm_size=True)
        check_parameters = [feat_target,
                            cell_target,
                            valid_ra_target,
                            valid_ca_target,
                            label_target]
        checked = utils.mimic_list(parameters=check_parameters,
                                   list_len=len(loom_target))
        feat_target = checked[0]
        cell_target = checked[1]
        valid_ra_target = checked[2]
        valid_ca_target = checked[3]
        label_target = checked[4]
        is_a_list = True
    elif isinstance(loom_target, str):
        utils.all_same_type_size(parameters=[loom_target,
                                             layer_target,
                                             cell_target,
                                             feat_target],
                                 expected_type='str',
                                 confirm_size=False)
    # Get data from source
    imputed_dat = [high_mem_get_data(loom_file=loom_source,
                                     layer=layer_source,
                                     feat_attr=feat_source,
                                     cell_attr=cell_source,
                                     valid_ra=valid_ra_source,
                                     valid_ca=valid_ca_source,
                                     remove_version=remove_version,
                                     verbose=verbose)]
    if is_type:
        type_dat = [high_mem_repeat_label(loom_file=loom_source,
                                          valid_ca=valid_ca_source,
                                          label=label_source)]
    file_dat = [high_mem_repeat_label(loom_file=loom_source,
                                      valid_ca=valid_ca_source,
                                      label=loom_source)]
    # Get data from target(s)
    if is_a_list:
        for i in np.arange(len(loom_target)):
            imputed_dat.append(high_mem_get_data(loom_file=loom_target[i],
                                                 layer=layer_target[i],
                                                 feat_attr=feat_target[i],
                                                 cell_attr=cell_target[i],
                                                 valid_ra=valid_ra_target[i],
                                                 valid_ca=valid_ca_target[i],
                                                 remove_version=remove_version,
                                                 verbose=verbose))
            if is_type:
                type_dat.append(high_mem_repeat_label(loom_file=loom_target[i],
                                                      valid_ca=valid_ca_target[i],
                                                      label=label_target[i]))
            file_dat.append(high_mem_repeat_label(loom_file=loom_target[i],
                                                  valid_ca=valid_ca_target[i],
                                                  label=loom_target[i]))
    else:
        imputed_dat.append(high_mem_get_data(loom_file=loom_target,
                                             layer=layer_target,
                                             feat_attr=feat_target,
                                             cell_attr=cell_target,
                                             valid_ra=valid_ra_target,
                                             valid_ca=valid_ca_target,
                                             remove_version=remove_version,
                                             verbose=verbose))
        if is_type:
            type_dat.append(high_mem_repeat_label(loom_file=loom_target,
                                                  valid_ca=valid_ca_target,
                                                  label=label_target))
        file_dat.append(high_mem_repeat_label(loom_file=loom_target,
                                              valid_ca=valid_ca_target,
                                              label=loom_target))
    # Combine data
    imputed_dat = pd.concat(imputed_dat, axis=0, sort=False)
    type_dat = np.hstack(type_dat)
    file_dat = np.hstack(file_dat)
    # Make output loom file
    col_attrs = {'CellID': imputed_dat.index.values,
                 'OriginalFile': file_dat}
    if is_type:
        col_attrs['Modality'] = type_dat
    row_attrs = {'Accession': imputed_dat.columns.values}
    imputed_dat = imputed_dat.T.values
    loompy.create(loom_output,
                  imputed_dat,
                  col_attrs=col_attrs,
                  row_attrs=row_attrs)
    if verbose:
        int_log.info('Integrated loom file is saved to {}'.format(loom_output))


def low_mem_integrate(loom_source,
                      loom_target,
                      loom_output,
                      layer_source='',
                      layer_target='',
                      feat_source='Accession',
                      feat_target='Accession',
                      cell_source='CellID',
                      cell_target='CellID',
                      label_source=None,
                      label_target=None,
                      valid_ra_source=None,
                      valid_ra_target=None,
                      valid_ca_source=None,
                      valid_ca_target=None,
                      remove_version=False,
                      batch_size=5000,
                      verbose=False):
    """
    Generates an integrated loom file containing observed and imputed counts for a single modality
        Is slow but uses low memory

    Args:
        loom_source (str): Path to source loom file (modality that loom_target is imputed into)
        loom_target (str/list): Path(s) to target files (modality/modalities that receive imputed counts)
        loom_output (str): Path to output loom file that contains observed/imputed counts for a given modality
        layer_source (str): Layer in loom_source containing observed counts
        layer_target (str/list): Layer(s) in loom_target containing imputed counts
        feat_source (str): Row attribute containing unique feature IDs in loom_source
            IDs will be included in loom_output under the row attribute Accession
        feat_target (str/list): Row attribute(s) containing unique feature IDs in loom_target
        cell_source (str): Column attribute containing unique cell IDs in loom_source
            IDs will be included in loom_output under the column attribute CellID
        cell_target (str/list): Column attribute(s) containing unique cell IDs in loom_target
        label_source (str/None): Optional, labels to be added to cells from loom_source
            Will be saved in the column attribute Modality
            If provided, label_target must also be provided
        label_target (str/list/None): Optional, labels to be added to cells from loom_target
        valid_ra_source (str/None): Optional, row attribute specifying features to include in loom_source
        valid_ra_target (str/list/None): Optional, row attribute specifying features to include in loom_target
        valid_ca_source (str/None): Optional, column attribute specifying cells to include in loom_source
        valid_ca_target (str/list/None): Optional, column attribute specifying cells to include in loom_target
        remove_version (bool): If true, remove GENCODE version ID
        batch_size (int): Number of cells to load per batch
        verbose (bool): If true, print logging messages
    """
    # Check inputs
    is_a_list = False
    if isinstance(loom_target, list):
        utils.all_same_type_size(parameters=[loom_target,
                                             layer_target],
                                 expected_type='list',
                                 confirm_size=True)
        check_parameters = [feat_target,
                            cell_target,
                            valid_ra_target,
                            valid_ca_target,
                            label_target]
        checked = utils.mimic_list(parameters=check_parameters,
                                   list_len=len(loom_target))
        feat_target = checked[0]
        cell_target = checked[1]
        valid_ra_target = checked[2]
        valid_ca_target = checked[3]
        label_target = checked[4]
        is_a_list = True
    elif isinstance(loom_target, str):
        utils.all_same_type_size(parameters=[loom_target,
                                             layer_target,
                                             cell_target,
                                             feat_target],
                                 expected_type='str',
                                 confirm_size=False)
    #  Get data from source
    low_mem_add_data(in_loom=loom_source,
                     out_loom=loom_output,
                     layer=layer_source,
                     feat_attr=feat_source,
                     cell_attr=cell_source,
                     label=label_source,
                     valid_ra=valid_ra_source,
                     valid_ca=valid_ca_source,
                     remove_version=remove_version,
                     batch_size=batch_size,
                     gen_out=True,
                     verbose=verbose)
    # Get data from target(s)
    if is_a_list:
        for i in np.arange(len(loom_target)):
            low_mem_add_data(in_loom=loom_target[i],
                             out_loom=loom_output,
                             layer=layer_target[i],
                             feat_attr=feat_target[i],
                             cell_attr=cell_target[i],
                             label=label_target[i],
                             valid_ra=valid_ra_target[i],
                             valid_ca=valid_ca_target[i],
                             remove_version=remove_version,
                             batch_size=batch_size,
                             gen_out=False,
                             verbose=verbose)
    else:
        low_mem_add_data(in_loom=loom_target,
                         out_loom=loom_output,
                         layer=layer_target,
                         feat_attr=feat_target,
                         cell_attr=cell_target,
                         label=label_target,
                         valid_ra=valid_ra_target,
                         valid_ca=valid_ca_target,
                         remove_version=remove_version,
                         batch_size=batch_size,
                         gen_out=False,
                         verbose=verbose)
    if verbose:
        int_log.info('Integrated loom file is saved to {}'.format(loom_output))


def integrate_data(loom_source,
                   loom_target,
                   loom_output,
                   layer_source='',
                   layer_target='',
                   feat_source='Accession',
                   feat_target='Accession',
                   cell_source='CellID',
                   cell_target='CellID',
                   label_source=None,
                   label_target=None,
                   valid_ra_source=None,
                   valid_ra_target=None,
                   valid_ca_source=None,
                   valid_ca_target=None,
                   remove_version=False,
                   low_mem=False,
                   batch_size=5000,
                   verbose=False):
    """
    Generates an integrated loom file containing observed and imputed counts for a single modality

    Args:
        loom_source (str): Path to source loom file (modality that loom_target is imputed into)
        loom_target (str/list): Path(s) to target files (modality/modalities that receive imputed counts)
        loom_output (str): Path to output loom file that contains observed/imputed counts for a given modality
        layer_source (str): Layer in loom_source containing observed counts
        layer_target (str/list): Layer(s) in loom_target containing imputed counts
        feat_source (str): Row attribute containing unique feature IDs in loom_source
            IDs will be included in loom_output under the row attribute Accession
        feat_target (str/list): Row attribute(s) containing unique feature IDs in loom_target
        cell_source (str): Column attribute containing unique cell IDs in loom_source
            IDs will be included in loom_output under the column attribute CellID
        cell_target (str/list): Column attribute(s) containing unique cell IDs in loom_target
        label_source (str/None): Optional, labels to be added to cells from loom_source
            Will be saved in the column attribute Modality
            If provided, label_target must also be provided
        label_target (str/list/None): Optional, labels to be added to cells from loom_target
        valid_ra_source (str/None): Optional, row attribute specifying features to include in loom_source
        valid_ra_target (str/list/None): Optional, row attribute specifying features to include in loom_target
        valid_ca_source (str/None): Optional, column attribute specifying cells to include in loom_source
        valid_ca_target (str/list/None): Optional, column attribute specifying cells to include in loom_target
        remove_version (bool): If true, remove GENCODE version ID
        low_mem (bool): If true, generate integrated loom file in batches to reduce memory
        batch_size (int): If low_mem, size of chunks
            A higher number will be faster but with a higher memory cost
        verbose (bool): If true, print logging messages
    """
    if low_mem:
        low_mem_integrate(loom_source=loom_source,
                          loom_target=loom_target,
                          loom_output=loom_output,
                          layer_source=layer_source,
                          layer_target=layer_target,
                          feat_source=feat_source,
                          feat_target=feat_target,
                          cell_source=cell_source,
                          cell_target=cell_target,
                          label_source=label_source,
                          label_target=label_target,
                          valid_ra_source=valid_ra_source,
                          valid_ra_target=valid_ra_target,
                          valid_ca_source=valid_ca_source,
                          valid_ca_target=valid_ca_target,
                          remove_version=remove_version,
                          batch_size=batch_size,
                          verbose=verbose)
    else:
        high_mem_integrate(loom_source=loom_source,
                           loom_target=loom_target,
                           loom_output=loom_output,
                           layer_source=layer_source,
                           layer_target=layer_target,
                           feat_source=feat_source,
                           feat_target=feat_target,
                           cell_source=cell_source,
                           cell_target=cell_target,
                           label_source=label_source,
                           label_target=label_target,
                           valid_ra_source=valid_ra_source,
                           valid_ra_target=valid_ra_target,
                           valid_ca_source=valid_ca_source,
                           valid_ca_target=valid_ca_target,
                           remove_version=remove_version,
                           verbose=verbose)
