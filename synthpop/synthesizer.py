import logging
import sys
from collections import namedtuple

import numpy as np
import pandas as pd
from scipy.stats import chisquare

from . import categorizer as cat
from . import draw
from .ipf.ipf import calculate_constraints
from .ipu.ipu import household_weights

logger = logging.getLogger("synthpop")
FitQuality = namedtuple(
    'FitQuality',
    ('people_chisq', 'people_p'))
BlockGroupID = namedtuple(
    'BlockGroupID', ('state', 'county', 'tract', 'block_group'))


def enable_logging():
    handler = logging.StreamHandler(stream=sys.stdout)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


def synthesize(h_marg, p_marg, h_jd, p_jd, h_pums, p_pums,
               marginal_zero_sub=.01, jd_zero_sub=.001, hh_index_start=0):

    # this is the zero marginal problem
    h_marg = h_marg.replace(0, marginal_zero_sub)
    p_marg = p_marg.replace(0, marginal_zero_sub)

    # zero cell problem
    h_jd.frequency = h_jd.frequency.replace(0, jd_zero_sub)
    p_jd.frequency = p_jd.frequency.replace(0, jd_zero_sub)

    # ipf for households
    logger.info("Running ipf for households")
    h_constraint, _ = calculate_constraints(h_marg, h_jd.frequency)
    h_constraint.index = h_jd.cat_id

    logger.debug("Household constraint")
    logger.debug(h_constraint)
    logger.debug(h_constraint.sum())

    # ipf for persons
    logger.info("Running ipf for persons")
    p_constraint, _ = calculate_constraints(p_marg, p_jd.frequency)
    p_constraint.index = p_jd.cat_id

    logger.debug("Person constraint")
    logger.debug(p_constraint)
    logger.debug(p_constraint.sum())

    # make frequency tables that the ipu expects
    household_freq, person_freq = cat.frequency_tables(p_pums, h_pums,
                                                       p_jd.cat_id,
                                                       h_jd.cat_id)

    # do the ipu to match person marginals
    logger.info("Running ipu")
    import time
    t1 = time.time()
    best_weights, fit_quality, iterations = household_weights(household_freq,
                                                              person_freq,
                                                              h_constraint,
                                                              p_constraint)
    logger.info("Time to run ipu: %.3fs" % (time.time()-t1))

    logger.debug("IPU weights:")
    logger.debug(best_weights.describe())
    logger.debug(best_weights.sum())
    logger.debug("Fit quality:")
    logger.debug(fit_quality)
    logger.debug("Number of iterations:")
    logger.debug(iterations)

    num_households = int(h_marg.groupby(level=0).sum().mean())
    print "Drawing %d households" % num_households

    best_chisq = np.inf

    return draw.draw_households(
        num_households, h_pums, p_pums, household_freq, h_constraint,
        p_constraint, best_weights, hh_index_start=hh_index_start)


def synthesize_all(recipe, num_geogs=None, indexes=None,
                   marginal_zero_sub=.01, jd_zero_sub=.001):
    """
    Parameters
    ----------
    write_households_csv, write_persons_csv : str
        Name of households and persons csv file to write.  
        Pass None to return these rather than write.
    
    Returns
    -------
    households, people : pandas.DataFrame
        Only returns these if `write_households_csv` and `write_persons_csv`
        are None.
    fit_quality : dict of FitQuality
        Keys are geographic IDs, values are namedtuples with attributes
        ``.household_chisq``, ``household_p``, ``people_chisq``,
        and ``people_p``.

    """
    print "Synthesizing at geog level: '{}' (number of geographies is {})".\
        format(recipe.get_geography_name(), recipe.get_num_geographies())

    if indexes is None:
        indexes = recipe.get_available_geography_ids()

    hh_list = []
    people_list = []
    cnt = 0
    fit_quality = {}
    hh_index_start = 0

    # TODO will parallelization work here?
    for geog_id in indexes:
        print "Synthesizing geog id:\n", geog_id

        h_marg = recipe.get_household_marginal_for_geography(geog_id)
        logger.debug("Household marginal")
        logger.debug(h_marg)

        p_marg = recipe.get_person_marginal_for_geography(geog_id)
        logger.debug("Person marginal")
        logger.debug(p_marg)

        h_pums, h_jd = recipe.\
            get_household_joint_dist_for_geography(geog_id)
        logger.debug("Household joint distribution")
        logger.debug(h_jd)

        p_pums, p_jd = recipe.get_person_joint_dist_for_geography(geog_id)
        logger.debug("Person joint distribution")
        logger.debug(p_jd)

        try:
            households, people, people_chisq, people_p = \
                synthesize(
                    h_marg, p_marg, h_jd, p_jd, h_pums, p_pums,
                    marginal_zero_sub=marginal_zero_sub, jd_zero_sub=jd_zero_sub,
                    hh_index_start=hh_index_start)

            if not recipe.write_households(households):
                hh_list.append(households)
            
            if not recipe.write_persons(people):
                people_list.append(people)
                
            key = tuple(geog_id.values)
            # key = BlockGroupID(
            #    geog_id['state'], geog_id['county'], geog_id['tract'],
            #    geog_id['block group'])
            fit_quality[key] = FitQuality(people_chisq, people_p)
    
            cnt += 1
            if len(households) > 0:
                hh_index_start = households.index.values[-1] + 1
    

            if num_geogs is not None and cnt >= num_geogs:
                break
        
        except Exception as e:
            print "Exception caught: ",e
            # continue

    return (pd.concat(hh_list) if len(hh_list) > 0 else None, 
            pd.concat(people_list, ignore_index=True) if len(people_list) > 0 else None,
            fit_quality)
