#
# Copyright (C) 2015-2020 University of Oxford
#
# This file is part of msprime.
#
# msprime is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# msprime is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with msprime.  If not, see <http://www.gnu.org/licenses/>.
#
"""
Test cases for basic ancestry simulation operations.
"""
import datetime
import json
import logging
import random
import sys
import unittest
import warnings

import numpy as np
import tskit

import msprime
from msprime import _msprime
from msprime import ancestry


def tree_sequences_equal(ts1, ts2):
    """
    Returns True if the specified tree sequences are equal, ignoring
    their provenances.
    """
    t1 = ts1.dump_tables()
    t2 = ts2.dump_tables()
    t1.provenances.clear()
    t2.provenances.clear()
    return t1 == t2


def has_discrete_genome(ts):
    """
    Returns True if the specified tree sequence has discrete genome coordinates.
    """
    tables = ts.tables
    edges_left = np.all(tables.edges.left == np.floor(tables.edges.left))
    edges_right = np.all(tables.edges.right == np.floor(tables.edges.right))
    migrations_left = np.all(tables.migrations.left == np.floor(tables.migrations.left))
    migrations_right = np.all(
        tables.migrations.right == np.floor(tables.migrations.right)
    )
    sites = np.all(tables.sites.position == np.floor(tables.sites.position))
    return edges_left and edges_right and migrations_left and migrations_right and sites


def get_bottleneck_examples():
    """
    Returns an iterator of example tree sequences with nonbinary
    trees.
    """
    bottlenecks = [
        msprime.SimpleBottleneck(0.01, 0, proportion=0.05),
        msprime.SimpleBottleneck(0.02, 0, proportion=0.25),
        msprime.SimpleBottleneck(0.03, 0, proportion=1),
    ]
    for n in [3, 10, 100]:
        ts = msprime.simulate(
            n,
            length=100,
            recombination_rate=1,
            demographic_events=bottlenecks,
            random_seed=n,
        )
        yield ts


class TestFullArg(unittest.TestCase):
    """
    Tests for recording the full ARG.
    """

    def verify(self, sim, multiple_mergers=False):
        sim.run()
        tree_sequence = next(sim.run_replicates(1))
        # Check if we have multiple merger somewhere.
        found = False
        for edgeset in tree_sequence.edgesets():
            if len(edgeset.children) > 2:
                found = True
                break
        self.assertEqual(multiple_mergers, found)

        flags = tree_sequence.tables.nodes.flags
        time = tree_sequence.tables.nodes.time
        # TODO add checks for migrations.
        re_nodes = np.where(flags == msprime.NODE_IS_RE_EVENT)[0]
        ca_nodes = np.where(flags == msprime.NODE_IS_CA_EVENT)[0]
        coal_nodes = np.where(flags == 0)[0]
        # There should be two recombination nodes for every event
        self.assertTrue(
            np.array_equal(time[re_nodes[::2]], time[re_nodes[1::2]])  # Even indexes
        )  # Odd indexes
        self.assertEqual(re_nodes.shape[0] / 2, sim.num_recombination_events)
        if not multiple_mergers:
            self.assertEqual(
                ca_nodes.shape[0] + coal_nodes.shape[0], sim.num_common_ancestor_events
            )
        # After simplification, all the RE and CA nodes should be gone.
        ts_simplified = tree_sequence.simplify()
        new_flags = ts_simplified.tables.nodes.flags
        new_time = ts_simplified.tables.nodes.time
        self.assertEqual(np.sum(new_flags == msprime.NODE_IS_RE_EVENT), 0)
        self.assertEqual(np.sum(new_flags == msprime.NODE_IS_CA_EVENT), 0)
        # All coal nodes from the original should be identical to the originals
        self.assertTrue(np.array_equal(time[coal_nodes], new_time[new_flags == 0]))
        self.assertLessEqual(ts_simplified.num_nodes, tree_sequence.num_nodes)
        self.assertLessEqual(ts_simplified.num_edges, tree_sequence.num_edges)
        return tree_sequence

    def test_no_recombination(self):
        rng = _msprime.RandomGenerator(1)
        sim = ancestry._parse_simulate(10, random_generator=rng, record_full_arg=True)
        ts = self.verify(sim)
        ts_simplified = ts.simplify()
        t1 = ts.tables
        t2 = ts_simplified.tables
        self.assertEqual(t1.nodes, t2.nodes)
        self.assertEqual(t1.edges, t2.edges)

    def test_recombination_n25(self):
        rng = _msprime.RandomGenerator(10)
        sim = ancestry._parse_simulate(
            25, recombination_rate=1, record_full_arg=True, random_generator=rng
        )
        self.verify(sim)

    def test_recombination_n5(self):
        rng = _msprime.RandomGenerator(10)
        sim = ancestry._parse_simulate(
            5, recombination_rate=10, record_full_arg=True, random_generator=rng
        )
        self.verify(sim)

    def test_recombination_n50(self):
        rng = _msprime.RandomGenerator(100)
        sim = ancestry._parse_simulate(
            50, recombination_rate=2, record_full_arg=True, random_generator=rng
        )
        self.verify(sim)

    def test_recombination_n100(self):
        rng = _msprime.RandomGenerator(100)
        sim = ancestry._parse_simulate(
            100, recombination_rate=0.2, record_full_arg=True, random_generator=rng
        )
        self.verify(sim)

    def test_multimerger(self):
        rng = _msprime.RandomGenerator(1234)
        sim = ancestry._parse_simulate(
            100,
            recombination_rate=0.1,
            record_full_arg=True,
            random_generator=rng,
            demographic_events=[
                msprime.InstantaneousBottleneck(time=0.1, population=0, strength=5)
            ],
        )
        self.verify(sim, multiple_mergers=True)


class TestSimulator(unittest.TestCase):
    """
    Runs tests on the underlying Simulator object.
    """

    def verify_simulation(self, n, m, r):
        """
        Verifies a simulation for the specified parameters.
        """
        recomb_map = msprime.RecombinationMap.uniform_map(m, r)
        rng = _msprime.RandomGenerator(1)
        sim = ancestry._parse_simulate(
            n, recombination_map=recomb_map, random_generator=rng, discrete_genome=True
        )
        self.assertEqual(sim.random_generator, rng)
        sim.run()
        self.assertEqual(sim.num_breakpoints, len(sim.breakpoints))
        self.assertGreater(sim.time, 0)
        self.assertGreater(sim.num_avl_node_blocks, 0)
        self.assertGreater(sim.num_segment_blocks, 0)
        self.assertGreater(sim.num_node_mapping_blocks, 0)
        tree_sequence = next(sim.run_replicates(1))
        t = 0.0
        for record in tree_sequence.nodes():
            if record.time > t:
                t = record.time
        self.assertEqual(sim.time, t)
        self.assertGreater(sim.num_common_ancestor_events, 0)
        self.assertGreaterEqual(sim.num_recombination_events, 0)
        self.assertGreaterEqual(np.sum(sim.num_migration_events), 0)
        self.assertGreaterEqual(sim.num_multiple_recombination_events, 0)

    def test_random_parameters(self):
        num_random_sims = 10
        for _ in range(num_random_sims):
            n = random.randint(2, 100)
            m = random.randint(10, 100)
            r = random.random()
            self.verify_simulation(n, m, r)

    def test_perf_parameters(self):
        sim = ancestry._parse_simulate(10)
        sim.run()
        self.assertGreater(sim.avl_node_block_size, 0)
        self.assertGreater(sim.segment_block_size, 0)
        self.assertGreater(sim.node_mapping_block_size, 0)

    def test_event_chunk(self):
        sim = ancestry._parse_simulate(10)
        for bad_chunk in [-(2 ** 32), -1, 0]:
            with self.assertRaises(ValueError):
                sim.run(event_chunk=bad_chunk)
        sim.reset()
        sim.run(event_chunk=2 ** 32 + 1)
        sim.reset()
        sim.run(event_chunk=2 ** 64 + 1)

    def test_debug_func(self):
        sim = ancestry._parse_simulate(10)
        count = 0

        def f(sim):
            nonlocal count
            count += 1

        sim.run(event_chunk=1, debug_func=f)
        self.assertGreater(count, 0)

    def test_info_logging(self):
        sim = ancestry._parse_simulate(10)
        with self.assertLogs("msprime.ancestry", logging.INFO) as log:
            sim.run()
            self.assertEqual(len(log.output), 2)
            self.assertEqual(
                log.output[0],
                (
                    "INFO:msprime.ancestry:Running model {'name': 'hudson'} "
                    "until max time: inf"
                ),
            )
            self.assertTrue(
                log.output[1].startswith("INFO:msprime.ancestry:Completed at time")
            )

    def test_debug_logging(self):
        sim = ancestry._parse_simulate(3)
        with self.assertLogs("msprime.ancestry", logging.DEBUG) as log:
            sim.run(event_chunk=1)
            self.assertEqual(len(log.output), 3)
            self.assertTrue(log.output[0].startswith("INFO"))
            self.assertTrue(log.output[-1].startswith("INFO"))
            self.assertTrue(log.output[1].startswith("DEBUG:msprime.ancestry:time="))

    def test_debug_logging_dtwf(self):
        sim = ancestry._parse_simulate(3, Ne=10, model="dtwf")
        with self.assertLogs("msprime.ancestry", logging.DEBUG) as log:
            sim.run(event_chunk=1)
            self.assertGreaterEqual(len(log.output), 3)
            self.assertTrue(log.output[0].startswith("INFO"))
            self.assertTrue(log.output[-1].startswith("INFO"))
            self.assertTrue(log.output[1].startswith("DEBUG:msprime.ancestry:time="))


class TestDemographyFactory(unittest.TestCase):
    """
    Tests fo the demography_factory function.
    """

    def test_mixed_old_and_new_style(self):
        demography = msprime.Demography()

        def f(
            population_configurations=None,
            migration_matrix=None,
            demographic_events=None,
        ):
            ancestry._demography_factory(
                Ne=1,
                demography=demography,
                population_configurations=population_configurations,
                migration_matrix=migration_matrix,
                demographic_events=demographic_events,
            )

        with self.assertRaises(ValueError):
            f(population_configurations=[])
        with self.assertRaises(ValueError):
            f(migration_matrix=[[]])
        with self.assertRaises(ValueError):
            f(demographic_events=[])

    def test_input_demography_copied(self):
        d1 = msprime.Demography.island_model(2, 1, Ne=100)
        d2 = ancestry._demography_factory(
            Ne=None,
            demography=d1,
            population_configurations=None,
            migration_matrix=None,
            demographic_events=None,
        )
        self.assertEqual(d1, d2)
        self.assertIsNot(d1, d2)
        self.assertIsNot(d1.populations[0], d2.populations[0])
        self.assertIsNot(d1.populations[1], d2.populations[1])
        self.assertIsNot(d1.migration_matrix, d2.migration_matrix)

    def test_Ne_does_not_override_demography(self):
        d1 = msprime.Demography.island_model(2, 1, Ne=100)
        self.assertEqual(d1.populations[0].initial_size, 100)
        self.assertEqual(d1.populations[1].initial_size, 100)
        d2 = ancestry._demography_factory(
            Ne=1234,
            demography=d1,
            population_configurations=None,
            migration_matrix=None,
            demographic_events=None,
        )
        self.assertEqual(d2.populations[0].initial_size, 100)
        self.assertEqual(d2.populations[1].initial_size, 100)

    def test_Ne_overwrites_size_none(self):
        d1 = msprime.Demography.island_model(2, 1, Ne=None)
        self.assertEqual(d1.populations[0].initial_size, None)
        self.assertEqual(d1.populations[1].initial_size, None)
        d2 = ancestry._demography_factory(
            Ne=1234,
            demography=d1,
            population_configurations=None,
            migration_matrix=None,
            demographic_events=None,
        )
        self.assertEqual(d2.populations[0].initial_size, 1234)
        self.assertEqual(d2.populations[1].initial_size, 1234)

        d1.populations[0].initial_size = 100
        d1.populations[1].initial_size = None
        d2 = ancestry._demography_factory(
            Ne=1234,
            demography=d1,
            population_configurations=None,
            migration_matrix=None,
            demographic_events=None,
        )
        self.assertEqual(d2.populations[0].initial_size, 100)
        self.assertEqual(d2.populations[1].initial_size, 1234)


class TestParseRandomSeed(unittest.TestCase):
    """
    Tests for parsing the random seed values.
    """

    def test_default(self):
        # Make sure we get different random seeds when calling sequentially.
        rngs = [ancestry._parse_random_seed(None) for _ in range(100)]
        self.assertEqual(len({rng.seed for rng in rngs}), len(rngs))
        self.assertTrue(all(isinstance(rng.seed, int) for rng in rngs))

    def test_numpy(self):
        seed = 12345
        rng = ancestry._parse_random_seed(np.array([seed], dtype=int)[0])
        self.assertEqual(rng.seed, seed)
        rng = ancestry._parse_random_seed(np.array([seed], dtype=int))
        self.assertEqual(rng.seed, seed)
        self.assertIsInstance(rng.seed, int)

    def test_ints(self):
        # Anything that can be cast to an int is fine.
        for seed in [1234, 12.0, "12"]:
            rng = ancestry._parse_random_seed(seed)
            self.assertEqual(rng.seed, int(seed))

    def test_bad_values(self):
        for bad_seed in [-1, 0, -10000]:
            with self.assertRaises(ValueError):
                ancestry._parse_random_seed(bad_seed)


class TestParseSimAncestry(unittest.TestCase):
    """
    Tests that the front-end for the sim_ancestry function correctly
    creates simulators with the required parameter values.
    """

    def test_random_generator(self):
        # Random seed is actually a special case in that it's handled by
        # the top-level code. But, we want to check it's handled correctly
        # here too so that we can rely on it for testing and so on.
        sim = ancestry._parse_sim_ancestry(10)
        rng = sim.random_generator
        self.assertIsInstance(rng, _msprime.RandomGenerator)
        self.assertNotEqual(rng.seed, 0)

        random_generator = _msprime.RandomGenerator(1234)
        sim = ancestry._parse_sim_ancestry(10, random_generator=random_generator)
        self.assertIs(sim.random_generator, random_generator)

        sim = ancestry._parse_sim_ancestry(10, random_seed=5678)
        self.assertIsInstance(sim.random_generator, _msprime.RandomGenerator)
        self.assertEqual(sim.random_generator.seed, 5678)

        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10, random_seed=5678, random_generator=random_generator
            )

    def test_sequence_length(self):
        # a single locus simulation will have sequence_length = 1
        sim = ancestry._parse_sim_ancestry(10)
        self.assertEqual(sim.sequence_length, 1)
        self.assertEqual(sim.copy_tables().sequence_length, 1)
        self.assertEqual(sim.recombination_map.total_mass, 0)
        self.assertEqual(sim.gene_conversion_map.total_mass, 0)

        # if we specify a rate_map for either GC or recomb this defines length.
        rate_map = msprime.RateMap.uniform(101, 0)
        sim = ancestry._parse_sim_ancestry(10, recombination_rate=rate_map)
        self.assertEqual(sim.sequence_length, rate_map.sequence_length)
        self.assertEqual(sim.copy_tables().sequence_length, sim.sequence_length)

        sim = ancestry._parse_sim_ancestry(
            10, gene_conversion_rate=rate_map, gene_conversion_track_length=1
        )
        self.assertEqual(sim.sequence_length, rate_map.sequence_length)
        self.assertEqual(sim.copy_tables().sequence_length, sim.sequence_length)

        # If we have an initial_state this defines sequence_length
        initial_state = tskit.TableCollection(1234)
        initial_state.populations.add_row()
        sim = ancestry._parse_sim_ancestry(initial_state=initial_state)
        self.assertEqual(sim.sequence_length, 1234)
        self.assertEqual(sim.copy_tables().sequence_length, sim.sequence_length)

    def test_sequence_length_errors(self):
        # scaler rate values with no squence length is an error
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, recombination_rate=1)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10, gene_conversion_rate=1, gene_conversion_track_length=1
            )

        # A rate map with a value that disagrees with sequence length
        rate_map = msprime.RateMap.uniform(101, 0)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10, recombination_rate=rate_map, sequence_length=1
            )
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
                sequence_length=1,
            )

        # A different rate map with a sequence_length that disagrees
        other_rate_map = msprime.RateMap.uniform(1, 0)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10,
                recombination_rate=other_rate_map,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
            )
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10,
                recombination_rate=other_rate_map,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
                sequence_length=other_rate_map.sequence_length,
            )
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10,
                recombination_rate=other_rate_map,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
                sequence_length=rate_map.sequence_length,
            )
        # Both maps disagree with sequence_length
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10,
                recombination_rate=other_rate_map,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
                sequence_length=56789,
            )

        # An initial state with a sequence_length that disagrees.
        initial_state = tskit.TableCollection(1234).tree_sequence()
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(initial_state=initial_state, sequence_length=1)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                initial_state=initial_state, recombination_rate=rate_map
            )
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                initial_state=initial_state,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
            )
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                initial_state=initial_state,
                recombination_rate=other_rate_map,
                gene_conversion_rate=rate_map,
                gene_conversion_track_length=1,
            )

    def test_sequence_length_discrete_genome(self):
        # Can't have floating point sequence_length with discrete_genome
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, sequence_length=1.1, discrete_genome=True)
        # Anything goes if we have a continuous genome, though.
        sim = ancestry._parse_sim_ancestry(
            10, sequence_length=0.1, discrete_genome=False
        )
        self.assertEqual(sim.sequence_length, 0.1)

    def test_sequence_length_bad_arguments(self):
        for bad_value in ["x", b"sdf"]:
            with self.assertRaises(ValueError):
                ancestry._parse_sim_ancestry(10, sequence_length=bad_value)

        for bad_type in [[], {}]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(10, sequence_length=bad_type)

    def test_gene_conversion_simple(self):
        for rate in ["1234", 1234, 1234.0]:
            sim = ancestry._parse_sim_ancestry(
                10,
                sequence_length=10,
                gene_conversion_rate=rate,
                gene_conversion_track_length=5,
            )
            self.assertEqual(sim.sequence_length, 10)
            gc_map = sim.gene_conversion_map
            self.assertEqual(gc_map.sequence_length, 10)
            self.assertEqual(len(gc_map), 1)
            self.assertEqual(gc_map.rate[0], 1234)
            self.assertEqual(sim.gene_conversion_track_length, 5)

    def test_gene_conversion_errors(self):
        # No track length is an error
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10, sequence_length=10, gene_conversion_rate=1234
            )
        # Specifying a track_length and no map is also an error.
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(
                10, sequence_length=10, gene_conversion_track_length=5.5
            )

        for bad_type in [[], {}]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(
                    10,
                    sequence_length=10,
                    gene_conversion_rate=bad_type,
                    gene_conversion_track_length=1,
                )

            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(
                    10,
                    sequence_length=10,
                    gene_conversion_rate=1,
                    gene_conversion_track_length=bad_type,
                )

    def test_discrete_genome(self):
        # default is True
        sim = ancestry._parse_sim_ancestry(10, sequence_length=10)
        self.assertTrue(sim.discrete_genome)
        for discrete_genome in [True, False]:
            sim = ancestry._parse_sim_ancestry(
                10, sequence_length=10, discrete_genome=discrete_genome
            )
            self.assertEqual(sim.discrete_genome, discrete_genome)
        # Falsey values are not OK
        for discrete_genome in ["", []]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(
                    10, sequence_length=10, discrete_genome=discrete_genome
                )

    def test_start_time(self):
        # default is 0
        sim = ancestry._parse_sim_ancestry(10)
        self.assertEqual(sim.start_time, 0)
        for start_time in [1234, 1234.34, "1", "1.234"]:
            sim = ancestry._parse_sim_ancestry(10, start_time=start_time)
            self.assertEqual(sim.start_time, float(start_time))
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, start_time="bad value")
        with self.assertRaises(TypeError):
            ancestry._parse_sim_ancestry(10, start_time=[])

    def test_end_time(self):
        # default is DBL_MAX
        sim = ancestry._parse_sim_ancestry(10)
        self.assertEqual(sim.end_time, sys.float_info.max)
        for end_time in [1234, 1234.34, "1", "1.234"]:
            sim = ancestry._parse_sim_ancestry(10, end_time=end_time)
            self.assertEqual(sim.end_time, float(end_time))
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, end_time="bad value")
        with self.assertRaises(TypeError):
            ancestry._parse_sim_ancestry(10, end_time=[])

    def test_record_migrations(self):
        # default is False
        sim = ancestry._parse_sim_ancestry(10)
        self.assertFalse(sim.record_migrations)
        for record_migrations in [True, False]:
            sim = ancestry._parse_sim_ancestry(10, record_migrations=record_migrations)
            self.assertEqual(sim.record_migrations, bool(record_migrations))
        for truthy in [0, []]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(10, record_migrations=truthy)

    def test_record_full_arg(self):
        # default is False
        sim = ancestry._parse_sim_ancestry(10)
        self.assertFalse(sim.record_full_arg)
        for record_full_arg in [True, False]:
            sim = ancestry._parse_sim_ancestry(10, record_full_arg=record_full_arg)
            self.assertEqual(sim.record_full_arg, bool(record_full_arg))

        for truthy in [0, []]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(10, record_full_arg=truthy)

    def test_ploidy(self):
        # default is 2
        sim = ancestry._parse_sim_ancestry(10)
        self.assertEqual(sim.ploidy, 2)
        for ploidy in [1, 2, "1", "33"]:
            sim = ancestry._parse_sim_ancestry(10, ploidy=ploidy)
            self.assertEqual(sim.ploidy, int(ploidy))

        for bad_ploidy in [0, -1]:
            with self.assertRaises(ValueError):
                ancestry._parse_sim_ancestry(10, ploidy=bad_ploidy)

        for bad_ploidy in ["0.1", 0.1, np.array([0.1])[0]]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(10, ploidy=bad_ploidy)

    def test_population_size(self):
        # default is 1
        sim = ancestry._parse_sim_ancestry(10)
        self.assertEqual(sim.demography.num_populations, 1)
        self.assertEqual(sim.demography.populations[0].initial_size, 1)
        self.assertEqual(sim.demography.populations[0].growth_rate, 0)
        for pop_size in [2, 0.1, 100, 1e6, "100"]:
            sim = ancestry._parse_sim_ancestry(10, population_size=pop_size)
            self.assertEqual(sim.demography.num_populations, 1)
            self.assertEqual(
                sim.demography.populations[0].initial_size, float(pop_size)
            )
            self.assertEqual(sim.demography.populations[0].growth_rate, 0)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, population_size=0)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, population_size=-1)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, population_size="bad value")
        with self.assertRaises(TypeError):
            ancestry._parse_sim_ancestry(10, population_size=[])

        # Cannot specify a population_size and demography args.
        demography = msprime.Demography.stepping_stone_1d(1, 0)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, demography=demography, population_size=1)

    def test_demography(self):
        demography = msprime.Demography.stepping_stone_1d(5, 0.1)
        samples = demography.sample(5)
        sim = ancestry._parse_sim_ancestry(samples, demography=demography)
        self.assertIs(sim.demography, demography)
        self.assertEqual(sim.num_populations, demography.num_populations)
        self.assertTrue(
            np.array_equal(sim.migration_matrix, demography.migration_matrix)
        )
        # Numeric samples fail here as we have more than 1 population
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(5, demography=demography)
        with self.assertRaises(TypeError):
            ancestry._parse_sim_ancestry(samples, demography="not a demography")

        demography.populations[0].initial_size = -1
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(samples, demography=demography)

    def test_model(self):
        # Extensive testing of the model parsing is done elsewhere.
        sim = ancestry._parse_sim_ancestry(10, model="smc")
        self.assertEqual(sim.model["name"], "smc")
        sim = ancestry._parse_sim_ancestry(10, model=("smc", (10, "hudson")))
        self.assertEqual(sim.model["name"], "smc")
        self.assertEqual(len(sim.model_change_events), 1)
        self.assertEqual(sim.model_change_events[0].time, 10)

    def test_dtwf_population_size(self):
        # It's an error to not specify a pop size for dtwf.
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, model="dtwf", ploidy=2)

    def test_negative_samples(self):
        for ploidy in [1, 2, 5]:
            with self.assertRaises(ValueError):
                ancestry._parse_sim_ancestry(-1, ploidy=ploidy)

    def test_numeric_samples(self):
        for n in [1, 3, 10]:
            for ploidy in [1, 2, 3]:
                sim = ancestry._parse_sim_ancestry(n, ploidy=ploidy)
                self.assertEqual(sim.ploidy, ploidy)
                tables = sim.copy_tables()
                self.assertEqual(len(tables.individuals), n)
                for individual in tables.individuals:
                    self.assertEqual(individual.flags, 0)
                    self.assertEqual(len(individual.location), 0)
                self.assertEqual(len(tables.nodes), n * ploidy)
                self.assertEqual(len(tables.populations), 1)
                for node_id, node in enumerate(tables.nodes):
                    self.assertEqual(node.individual, node_id // ploidy)
                    self.assertEqual(node.time, 0)
                    self.assertEqual(node.flags, tskit.NODE_IS_SAMPLE)
                    self.assertEqual(node.population, 0)

    def test_numeric_samples_types(self):
        # Make sure the various different ways we can specify a numeric
        # set of samples all give the same answer
        values = [10, 10.0, np.array([10], dtype=int)[0]]
        for value in values:
            sim = ancestry._parse_sim_ancestry(value)
            self.assertEqual(len(sim.copy_tables().individuals), 10)

    def test_numeric_samples_only_simple_demography(self):
        # A simple 1-population model is fine.
        demography = msprime.Demography.simple_model()
        sim = ancestry._parse_sim_ancestry(10, demography=demography)
        self.assertEqual(len(sim.copy_tables().individuals), 10)

        demography = msprime.Demography.stepping_stone_1d(2, 0)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(10, demography=demography)

    def verify_samples(self, samples, demography, ploidy):
        sim = ancestry._parse_sim_ancestry(
            samples=samples, demography=demography, ploidy=ploidy
        )
        self.assertEqual(sim.ploidy, ploidy)
        tables = sim.copy_tables()
        self.assertEqual(len(tables.individuals), len(samples))
        self.assertEqual(len(tables.nodes), len(samples) * ploidy)
        self.assertEqual(len(tables.populations), demography.num_populations)
        for node_id, node in enumerate(tables.nodes):
            ind_id = node_id // ploidy
            self.assertEqual(node.individual, ind_id)
            self.assertEqual(node.time, samples[ind_id].time)
            self.assertEqual(node.population, samples[ind_id].population)
            self.assertEqual(node.flags, tskit.NODE_IS_SAMPLE)
        for individual in tables.individuals:
            self.assertEqual(individual.flags, 0)
            self.assertEqual(len(individual.location), 0)

    def test_sample_demography(self):
        demography = msprime.Demography.simple_model()
        self.verify_samples(demography.sample(10), demography, ploidy=1)
        self.verify_samples(demography.sample(10), demography, ploidy=2)

        demography = msprime.Demography.stepping_stone_1d(5, 0)
        samples = demography.sample(1, 2, 3, 4, 5)
        self.verify_samples(samples, demography, ploidy=1)
        self.verify_samples(samples, demography, ploidy=2)

        samples = demography.sample(0, 0, 0, 0, 15)
        self.verify_samples(samples, demography, ploidy=1)
        self.verify_samples(samples, demography, ploidy=2)

    def test_sample_time(self):
        demography = msprime.Demography.stepping_stone_1d(2, 0)
        samples = [msprime.Sample(time=j, population=j % 2) for j in range(10)]
        self.verify_samples(samples, demography, ploidy=1)
        self.verify_samples(samples, demography, ploidy=2)

    def test_bad_samples(self):
        demography = msprime.Demography.stepping_stone_1d(2, 0)
        for bad_pop in [-1, 2]:
            samples = [msprime.Sample(time=0, population=bad_pop)] * 2
            with self.assertRaises(ValueError):
                ancestry._parse_sim_ancestry(samples=samples, demography=demography)
        for bad_pop in ["sdf", 1.1]:
            samples = [msprime.Sample(time=0, population=bad_pop)] * 2
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(samples=samples, demography=demography)

    def test_bad_sample_types(self):
        bad_sample_types = ["samples", "10", [0], np.array([0, 1]), ValueError]
        for bad_sample_type in bad_sample_types:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(bad_sample_type)

    def test_samples_and_initial_state(self):
        # If we specify neither samples of initial_state we get an error
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(samples=None)

        # Specifying both is also an error.
        tables = tskit.TableCollection(1)
        tables.populations.add_row()
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(2, initial_state=tables)

    def test_initial_state_errors(self):
        tables = tskit.TableCollection(1)
        tables.populations.add_row()
        # sequence_length doesn't match.
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(initial_state=tables, sequence_length=100)
        # Must have at least one population
        tables = tskit.TableCollection(1)
        with self.assertRaises(ValueError):
            ancestry._parse_sim_ancestry(initial_state=tables)
        for bad_type in [[], "sdf", {}]:
            with self.assertRaises(TypeError):
                ancestry._parse_sim_ancestry(initial_state=bad_type)

    def test_initial_state(self):
        ts = msprime.sim_ancestry(10, end_time=0.01, random_seed=2)
        # Same if we use either the tables or tree sequence object.
        sim = ancestry._parse_sim_ancestry(initial_state=ts)
        self.assertTrue(sim.copy_tables() == ts.tables)
        sim = ancestry._parse_sim_ancestry(initial_state=ts.tables)
        self.assertTrue(sim.copy_tables() == ts.tables)

    def test_num_labels(self):
        for num_labels in [1, 2, 10]:
            sim = ancestry._parse_sim_ancestry(10, num_labels=num_labels)
            self.assertEqual(sim.num_labels, num_labels)


class TestParseSimulate(unittest.TestCase):
    """
    Tests that the front-end for the simulate function correctly
    creates simulators with the required parameter values.
    """

    def test_default_random_seed(self):
        sim = ancestry._parse_simulate(10)
        rng = sim.random_generator
        self.assertIsInstance(rng, _msprime.RandomGenerator)
        self.assertNotEqual(rng.seed, 0)

    def test_random_generator(self):
        seed = 12345
        rng = _msprime.RandomGenerator(seed)
        sim = ancestry._parse_simulate(10, random_generator=rng)
        self.assertEqual(rng, sim.random_generator)
        self.assertEqual(rng.seed, seed)

    def test_random_seed(self):
        seed = 12345
        sim = ancestry._parse_simulate(10, random_seed=seed)
        self.assertEqual(sim.random_generator.seed, seed)

        # It's an error to specify both seed and generator.
        with self.assertRaises(ValueError):
            ancestry._parse_simulate(
                10, random_seed=1234, random_generator=_msprime.RandomGenerator(1234)
            )

    def test_length(self):
        for bad_length in [-1, 0, -1e-6]:
            with self.assertRaises(ValueError):
                ancestry._parse_simulate(10, length=bad_length)

    def test_num_labels(self):
        for bad_value in [-1, 0, 0.1]:
            with self.assertRaises(ValueError):
                ancestry._parse_simulate(10, num_labels=bad_value)

    def test_sample_size(self):
        self.assertRaises(ValueError, ancestry._parse_simulate)
        self.assertRaises(ValueError, ancestry._parse_simulate, 1)
        self.assertRaises(ValueError, ancestry._parse_simulate, sample_size=1)
        for n in [2, 100, 1000]:
            sim = ancestry._parse_simulate(n)
            tables = sim.copy_tables()
            self.assertEqual(len(tables.populations), 1)
            self.assertEqual(len(tables.individuals), 0)
            self.assertEqual(len(tables.edges), 0)
            self.assertEqual(len(tables.nodes), n)
            self.assertTrue(np.all(tables.nodes.flags == tskit.NODE_IS_SAMPLE))
            self.assertTrue(np.all(tables.nodes.time == 0))
            self.assertTrue(np.all(tables.nodes.individual == tskit.NULL))

    def test_effective_population_size(self):
        def f(Ne):
            return ancestry._parse_simulate(10, Ne=Ne)

        for bad_value in [-1, -1e16, 0]:
            self.assertRaises(ValueError, f, bad_value)
        for Ne in [1, 10, 1e5]:
            sim = f(Ne)
            self.assertEqual(sim.demography.populations[0].initial_size, Ne)
        # Test the default.
        sim = ancestry._parse_simulate(10)
        self.assertEqual(sim.demography.populations[0].initial_size, 1)

    def test_ploidy(self):
        for ploidy in [1, 2, 7]:
            sim = ancestry._parse_simulate(10, ploidy=ploidy)
            self.assertEqual(sim.ploidy, ploidy)

    def test_discrete_genome_continuous_length(self):
        for bad_length in [0.1, 1.1, 1000.1]:
            with self.assertRaises(ValueError):
                ancestry._parse_simulate(10, discrete_genome=True, length=bad_length)

    def test_population_configurations(self):
        def f(configs):
            return ancestry._parse_simulate(population_configurations=configs)

        for bad_type in [10, ["sdf"], "sdfsd"]:
            self.assertRaises(TypeError, f, bad_type)
        # Just test the basic equalities here. The actual
        # configuration options are tested elewhere.
        for N in range(1, 10):
            pop_configs = [
                msprime.PopulationConfiguration(5, initial_size=5) for _ in range(N)
            ]
            sample_size = 5 * N
            sim = ancestry._parse_simulate(population_configurations=pop_configs)
            self.assertEqual(len(sim.demography.populations), len(pop_configs))
            for pop, pop_config in zip(sim.demography.populations, pop_configs):
                self.assertEqual(pop.initial_size, pop_config.initial_size)
                self.assertEqual(pop.growth_rate, pop_config.growth_rate)
            tables = sim.copy_tables()
            self.assertEqual(len(tables.nodes), sample_size)
            self.assertEqual(len(sim.population_configuration), N)
        # The default is a single population
        sim = ancestry._parse_simulate(10)
        self.assertEqual(len(sim.population_configuration), 1)

    def test_sample_size_population_configuration(self):
        for d in range(1, 5):
            # Zero sample size is always an error
            configs = [msprime.PopulationConfiguration(0) for _ in range(d)]
            self.assertRaises(
                ValueError, ancestry._parse_simulate, population_configurations=configs
            )
            configs = [msprime.PopulationConfiguration(2) for _ in range(d)]
            sim = ancestry._parse_simulate(population_configurations=configs)
            tables = sim.copy_tables()
            self.assertEqual(len(tables.nodes), 2 * d)
            i = 0
            for j in range(d):
                for _ in range(2):
                    node = tables.nodes[i]
                    self.assertEqual(node.population, j)
                    self.assertEqual(node.time, 0)
                    self.assertEqual(node.flags, tskit.NODE_IS_SAMPLE)
                    i += 1

    def test_migration_matrix(self):
        # Cannot specify a migration matrix without population
        # configurations
        self.assertRaises(ValueError, ancestry._parse_simulate, 10, migration_matrix=[])
        for N in range(1, 10):
            pop_configs = [msprime.PopulationConfiguration(5) for _ in range(N)]
            sim = ancestry._parse_simulate(population_configurations=pop_configs)
            # If we don't specify a matrix, it's 0 everywhere.
            matrix = np.zeros((N, N))
            np.testing.assert_array_equal(sim.migration_matrix, matrix)

            def f(matrix):
                return ancestry._parse_simulate(
                    population_configurations=pop_configs, migration_matrix=matrix
                )

            matrix = [[(j + k) * int(j != k) for j in range(N)] for k in range(N)]
            sim = f(matrix)
            np.testing.assert_array_equal(sim.demography.migration_matrix, matrix)
            # Try with equivalent numpy array.
            sim = f(np.array(matrix))
            np.testing.assert_array_equal(sim.demography.migration_matrix, matrix)
            np.testing.assert_array_equal(sim.migration_matrix, matrix)
            for bad_type in [{}, "", 234, 1.2]:
                self.assertRaises(ValueError, f, bad_type)
            # Now check for the structure of the matrix.
            matrix[0][0] = "bad value"
            self.assertRaises(ValueError, f, matrix)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                matrix[0] = None
                self.assertRaises(ValueError, f, matrix)
                matrix[0] = []
                self.assertRaises(ValueError, f, matrix)
            # Simple numpy array.
            matrix = np.ones((N, N))
            np.fill_diagonal(matrix, 0)
            sim = f(matrix)
            np.testing.assert_array_equal(
                np.array(sim.demography.migration_matrix), matrix
            )
            sim.run()
            events = np.array(sim.num_migration_events)
            self.assertEqual(events.shape, (N, N))
            self.assertTrue(np.all(events >= 0))

    def test_default_migration_matrix(self):
        sim = ancestry._parse_simulate(10)
        self.assertEqual(sim.migration_matrix, [0.0])

    def test_demographic_events(self):
        for bad_type in ["sdf", 234, [12], [None]]:
            self.assertRaises(
                TypeError, ancestry._parse_simulate, 2, demographic_events=bad_type
            )
        # TODO test for bad values.

    def test_recombination_rate(self):
        def f(recomb_rate):
            return ancestry._parse_simulate(10, recombination_rate=recomb_rate)

        for bad_type in ["", {}, []]:
            self.assertRaises(TypeError, f, bad_type)
        for bad_value in [-1, -1e15]:
            self.assertRaises(ValueError, f, bad_value)
        for rate in [0, 1e-3, 10]:
            sim = f(rate)
            recomb_map = sim.recombination_map
            self.assertEqual(list(recomb_map.position), [0, 1])
            self.assertEqual(list(recomb_map.rate), [rate])
            self.assertEqual(sim.sequence_length, recomb_map.sequence_length)

    def test_recombination_map(self):
        def f(recomb_map):
            return ancestry._parse_simulate(10, recombination_map=recomb_map)

        self.assertRaises(TypeError, f, "wrong type")
        for n in range(2, 10):
            positions = list(range(n))
            rates = [0.1 * j for j in range(n - 1)]
            # Use the old-form RecombinationMap
            recomb_map = msprime.RecombinationMap(positions, rates + [0.0])
            sim = ancestry._parse_simulate(10, recombination_map=recomb_map)
            other_map = sim.recombination_map
            self.assertEqual(list(other_map.position), positions)
            self.assertEqual(list(other_map.rate), rates)
            self.assertEqual(sim.sequence_length, other_map.sequence_length)
            # Use the new-form RateMap
            rate_map = msprime.RateMap(positions, rates)
            sim = ancestry._parse_simulate(10, recombination_map=rate_map)
            other_map = sim.recombination_map
            self.assertEqual(list(other_map.position), positions)
            self.assertEqual(list(other_map.rate), rates)
            self.assertEqual(sim.sequence_length, other_map.sequence_length)

    def test_combining_recomb_map_and_rate_length(self):
        recomb_map = msprime.RecombinationMap([0, 1], [1, 0])
        self.assertRaises(
            ValueError,
            ancestry._parse_simulate,
            10,
            recombination_map=recomb_map,
            length=1,
        )
        self.assertRaises(
            ValueError,
            ancestry._parse_simulate,
            10,
            recombination_map=recomb_map,
            recombination_rate=100,
        )
        self.assertRaises(
            ValueError,
            ancestry._parse_simulate,
            10,
            recombination_map=recomb_map,
            length=1,
            recombination_rate=1,
        )

    def test_sample_combination_errors(self):
        # Make sure that the various ways we can specify the samples
        # operate correctly.
        s = msprime.Sample(time=0.0, population=0)
        self.assertRaises(ValueError, ancestry._parse_simulate)
        # Cannot provide sample_size with either population configurations
        # or samples
        self.assertRaises(
            ValueError, ancestry._parse_simulate, sample_size=2, samples=[s, s]
        )
        pop_configs = [msprime.PopulationConfiguration(sample_size=2)]
        self.assertRaises(
            ValueError,
            ancestry._parse_simulate,
            sample_size=2,
            population_configurations=pop_configs,
        )
        # If we provide samples and population_configurations we cannot
        # have a sample size for the config.
        pop_configs = [msprime.PopulationConfiguration(sample_size=2)]
        self.assertRaises(
            ValueError,
            ancestry._parse_simulate,
            samples=[s, s],
            population_configurations=pop_configs,
        )
        pop_configs = [
            msprime.PopulationConfiguration(sample_size=None),
            msprime.PopulationConfiguration(sample_size=2),
        ]
        self.assertRaises(
            ValueError,
            ancestry._parse_simulate,
            samples=[s, s],
            population_configurations=pop_configs,
        )

    def test_samples(self):
        pop_configs = [
            msprime.PopulationConfiguration(),
            msprime.PopulationConfiguration(),
            msprime.PopulationConfiguration(),
        ]
        samples = [
            msprime.Sample(population=0, time=0),
            msprime.Sample(population=1, time=1),
            msprime.Sample(population=2, time=2),
        ]
        sim = ancestry._parse_simulate(
            samples=samples, population_configurations=pop_configs
        )
        tables = sim.copy_tables()
        self.assertEqual(len(tables.nodes), len(samples))
        for node, sample in zip(tables.nodes, samples):
            self.assertEqual(node.population, sample.population)
            self.assertEqual(node.time, sample.time)
            self.assertEqual(node.flags, tskit.NODE_IS_SAMPLE)

    def test_new_old_style_model_changes_equal(self):
        models = [
            msprime.SweepGenicSelection(
                position=j, start_frequency=j, end_frequency=j, alpha=j, dt=j
            )
            for j in range(1, 10)
        ]
        # Old style
        sim = ancestry._parse_simulate(
            sample_size=2,
            Ne=10,
            demographic_events=[
                msprime.SimulationModelChange(None, model) for model in models
            ],
        )
        self.assertEqual(len(sim.model_change_events), len(models))
        for event, model in zip(sim.model_change_events, models):
            self.assertEqual(event.model, model)

        sim2 = ancestry._parse_simulate(
            sample_size=2,
            Ne=10,
            model=[None]
            + [msprime.SimulationModelChange(None, model) for model in models],
        )
        self.assertEqual(sim.model_change_events, sim2.model_change_events)

    def test_model_change_old_style(self):
        main_model = msprime.SmcApproxCoalescent()
        sim = ancestry._parse_simulate(
            Ne=100,
            sample_size=2,
            model=main_model,
            demographic_events=[
                msprime.SimulationModelChange(1, msprime.DiscreteTimeWrightFisher()),
                msprime.SimulationModelChange(2, None),
            ],
        )
        self.assertEqual(len(sim.model_change_events), 2)
        self.assertEqual(sim.model_change_events[0].time, 1)
        # When model=None we change to the standard coalescent
        self.assertEqual(sim.model_change_events[1].time, 2)
        self.assertEqual(sim.model_change_events[1].model.name, "hudson")

        # This should be the same in new notation
        sim = ancestry._parse_simulate(
            Ne=100, sample_size=2, model=[main_model, (1, "dtwf"), (2, None)]
        )
        self.assertEqual(len(sim.model_change_events), 2)
        self.assertEqual(sim.model_change_events[0].time, 1)
        # When model=None we change to the standard coalescent
        self.assertEqual(sim.model_change_events[1].time, 2)
        self.assertEqual(sim.model_change_events[1].model.name, "hudson")

    def test_bad_sample_population_reference(self):
        # What happens when we reference a population that doesn't exist?
        with self.assertRaises(ValueError) as ve:
            msprime.simulate(
                samples=[
                    msprime.Sample(population=0, time=0),
                    msprime.Sample(population=1, time=0),
                ]
            )
        self.assertEqual(
            str(ve.exception), "Invalid population reference '1' in sample at index 1"
        )

        with self.assertRaises(ValueError) as ve:
            msprime.simulate(
                samples=[
                    msprime.Sample(population=0, time=0),
                    msprime.Sample(population=0, time=0),
                    msprime.Sample(population=-1, time=0),
                ]
            )
        self.assertEqual(
            str(ve.exception), "Negative population ID in sample at index 2"
        )


class TestSimAncestryInterface(unittest.TestCase):
    """
    Some simple tests cases for the sim_ancestry interface.
    """

    def test_defaults(self):
        n = 10
        # Diploid sim by default.
        ts = msprime.sim_ancestry(n)
        self.assertEqual(ts.num_samples, 2 * n)
        self.assertEqual(ts.num_individuals, n)
        self.assertEqual(ts.num_trees, 1)
        self.assertEqual(ts.num_sites, 0)
        self.assertEqual(ts.sequence_length, 1)

    def test_ploidy(self):
        n = 10
        for k in [1, 2, 3, 4]:
            ts = msprime.sim_ancestry(n, ploidy=k)
            self.assertEqual(ts.num_samples, k * n)
            self.assertEqual(ts.num_trees, 1)
            self.assertEqual(ts.num_sites, 0)
            self.assertEqual(ts.sequence_length, 1)
            # TODO check for individuals

    def test_hudson_time_scale(self):
        n = 10
        seed = 1234
        for ploidy in [1, 2, 3, 7]:
            ts1 = msprime.sim_ancestry(n * ploidy, ploidy=1, random_seed=seed)
            ts2 = msprime.sim_ancestry(n, ploidy=ploidy, random_seed=seed)
            t1 = ts1.tables
            t2 = ts2.tables
            self.assertTrue(np.allclose(t1.nodes.time * ploidy, t2.nodes.time))
            self.assertEqual(t1.edges, t2.edges)

    def test_ploidy_demography(self):
        n = 2
        demography = msprime.Demography.island_model(2, 0.1)
        for k in [1, 2, 3, 4]:
            samples = demography.sample(n, n)
            ts = msprime.sim_ancestry(samples=samples, ploidy=k, demography=demography)
            self.assertEqual(ts.num_samples, len(samples) * k)
            self.assertEqual(ts.num_trees, 1)
            self.assertEqual(ts.num_sites, 0)
            self.assertEqual(ts.sequence_length, 1)
            self.assertEqual(ts.num_populations, 2)
            # TODO check for individuals

    def test_random_seed(self):
        ts1 = msprime.sim_ancestry(10, random_seed=1)
        ts2 = msprime.sim_ancestry(10, random_seed=1)
        self.assertTrue(tree_sequences_equal(ts1, ts2))

        ts2 = msprime.sim_ancestry(10, random_seed=2)
        self.assertFalse(tree_sequences_equal(ts1, ts2))

    def test_population_size(self):
        ts1 = msprime.sim_ancestry(10, population_size=1, random_seed=2)
        # Defaults to 1
        ts2 = msprime.sim_ancestry(10, random_seed=2)
        self.assertTrue(tree_sequences_equal(ts1, ts2))
        ts2 = msprime.sim_ancestry(10, population_size=100, random_seed=2)
        # Acts as a simple scaling factor on times.
        self.assertEqual(ts1.tables.edges, ts2.tables.edges)
        self.assertTrue(np.allclose(100 * ts1.tables.nodes.time, ts2.tables.nodes.time))

    def test_replicates(self):
        ts = msprime.simulate(10)
        self.assertIsInstance(ts, tskit.TreeSequence)
        for n in [0, 1, 2, 5]:
            ts_list = list(msprime.sim_ancestry(10, num_replicates=n))
            self.assertEqual(len(ts_list), n)
            for ts in ts_list:
                self.assertIsInstance(ts, tskit.TreeSequence)
                self.assertEqual(ts.num_individuals, 10)
                self.assertEqual(ts.num_samples, 20)
                self.assertEqual(ts.num_trees, 1)

    def test_recombination_rate(self):
        ts = msprime.sim_ancestry(10, recombination_rate=1, sequence_length=10)
        self.assertEqual(ts.num_samples, 20)
        self.assertEqual(ts.sequence_length, 10)
        self.assertGreater(ts.num_trees, 1)
        self.assertTrue(has_discrete_genome(ts))
        # A non-zero recombination_rate and no sequence length is an error
        with self.assertRaises(ValueError):
            msprime.sim_ancestry(10, recombination_rate=1)
        # But if we specify a rate map, that's OK.
        rate_map = msprime.RateMap.uniform(sequence_length=10, rate=1)
        ts = msprime.sim_ancestry(10, recombination_rate=rate_map)
        self.assertEqual(ts.num_samples, 20)
        self.assertEqual(ts.sequence_length, 10)
        self.assertGreater(ts.num_trees, 1)
        self.assertTrue(has_discrete_genome(ts))

        # We should get precisely the same ts if we have the same seed
        ts1 = msprime.sim_ancestry(
            10, recombination_rate=1, sequence_length=10, random_seed=1
        )
        ts2 = msprime.sim_ancestry(10, recombination_rate=rate_map, random_seed=1)
        self.assertTrue(tree_sequences_equal(ts1, ts2))

    def test_gc_rate(self):
        ts = msprime.sim_ancestry(
            10,
            gene_conversion_rate=1,
            gene_conversion_track_length=2,
            sequence_length=10,
            random_seed=14,
        )
        self.assertGreater(ts.num_trees, 1)
        for tree in ts.trees():
            self.assertEqual(tree.num_roots, 1)

    def test_model(self):
        ts1 = msprime.sim_ancestry(10, population_size=100, random_seed=2)
        ts2 = msprime.sim_ancestry(
            10, population_size=100, model="hudson", random_seed=2
        )
        self.assertTrue(tree_sequences_equal(ts1, ts2))
        ts2 = msprime.sim_ancestry(
            10, population_size=100, model="dtwf", random_seed=2, ploidy=2
        )
        self.assertFalse(tree_sequences_equal(ts1, ts2))

    def test_continuous_genome(self):
        ts = msprime.sim_ancestry(
            10, recombination_rate=10, sequence_length=1, discrete_genome=False
        )
        self.assertEqual(ts.num_samples, 20)
        self.assertEqual(ts.sequence_length, 1)
        self.assertGreater(ts.num_trees, 1)
        self.assertFalse(has_discrete_genome(ts))

    def test_discrete_genome(self):
        # Default to discrete_genome=True
        ts = msprime.sim_ancestry(
            10, recombination_rate=10, sequence_length=10, random_seed=2
        )
        self.assertGreater(ts.num_trees, 1)
        self.assertTrue(has_discrete_genome(ts))

        ts = msprime.sim_ancestry(
            10,
            recombination_rate=10,
            sequence_length=10,
            random_seed=2,
            discrete_genome=True,
        )
        self.assertGreater(ts.num_trees, 1)
        self.assertTrue(has_discrete_genome(ts))

        ts = msprime.sim_ancestry(
            10,
            recombination_rate=1,
            sequence_length=10,
            random_seed=2,
            discrete_genome=False,
        )
        self.assertGreater(ts.num_trees, 1)
        self.assertFalse(has_discrete_genome(ts))

    def test_record_provenance(self):
        # The content of the provenances is tested elsewhere.
        ts = msprime.sim_ancestry(10, random_seed=2)
        self.assertEqual(ts.num_provenances, 1)
        ts = msprime.sim_ancestry(10, random_seed=2, record_provenance=False)
        self.assertEqual(ts.num_provenances, 0)

    def test_replicate_index(self):
        n = 10
        ts_list = list(
            msprime.sim_ancestry(
                10, random_seed=42, num_replicates=n, record_provenance=False
            )
        )
        for j in range(n):
            ts = msprime.sim_ancestry(
                10, random_seed=42, replicate_index=j, record_provenance=False
            )
            self.assertEqual(ts.tables, ts_list[j].tables)

    def test_dtwf(self):
        ts = msprime.sim_ancestry(
            10, population_size=100, model="dtwf", ploidy=2, random_seed=1234
        )
        self.assertEqual(ts.num_trees, 1)
        self.assertEqual(ts.first().num_roots, 1)
        # All node times should be integers
        time = ts.tables.nodes.time
        self.assertTrue(np.all(time == np.floor(time)))

    def test_dtwf_non_diploid(self):
        for ploidy in [1, 3, 7]:
            with self.assertRaises(_msprime.LibraryError):
                msprime.sim_ancestry(
                    10, population_size=100, model="dtwf", ploidy=ploidy
                )

    def test_sweep_coalescence(self):
        N = 1e6
        model = msprime.SweepGenicSelection(
            position=0.5,
            start_frequency=1.0 / (2 * N),
            end_frequency=1.0 - (1.0 / (2 * N)),
            alpha=1000,
            dt=1e-6,
        )
        ts = msprime.sim_ancestry(10, model=model)
        self.assertEqual(ts.num_trees, 1)
        self.assertEqual(ts.first().num_roots, 1)

    def test_start_time(self):
        ts = msprime.sim_ancestry(10, ploidy=1, random_seed=42, start_time=100)
        self.assertTrue(np.all(ts.tables.nodes.time[10:] > 100))

    def test_end_time(self):
        ts = msprime.sim_ancestry(10, random_seed=42, end_time=0.01)
        self.assertTrue(np.all(ts.tables.nodes.time <= 0.01))
        self.assertGreater(ts.first().num_roots, 1)

    def test_record_migrations(self):
        demography = msprime.Demography.stepping_stone_1d(2, 0.1)
        samples = demography.sample(2, 2)
        ts = msprime.sim_ancestry(samples, demography=demography, random_seed=42)
        self.assertEqual(ts.first().num_roots, 1)
        # Migrations are off by default
        self.assertEqual(ts.num_migrations, 0)

        ts = msprime.sim_ancestry(
            samples, demography=demography, random_seed=42, record_migrations=True
        )
        self.assertEqual(ts.first().num_roots, 1)
        # Migrations are off by default
        self.assertGreater(ts.num_migrations, 0)

        ts = msprime.sim_ancestry(
            samples, demography=demography, random_seed=42, record_migrations=False
        )
        self.assertEqual(ts.first().num_roots, 1)
        self.assertEqual(ts.num_migrations, 0)

    def test_record_full_arg(self):
        ts = msprime.sim_ancestry(
            4,
            recombination_rate=1,
            random_seed=2,
            sequence_length=10,
            record_full_arg=True,
        )
        flags = ts.tables.nodes.flags
        self.assertGreater(np.sum(flags == msprime.NODE_IS_RE_EVENT), 0)
        for record_full_arg in [None, False]:
            ts = msprime.sim_ancestry(
                4,
                recombination_rate=1,
                random_seed=2,
                sequence_length=10,
                record_full_arg=record_full_arg,
            )
            flags = ts.tables.nodes.flags
            self.assertEqual(np.sum(flags == msprime.NODE_IS_RE_EVENT), 0)

    def test_initial_tables_recapitate(self):
        # Simple recapitate scenario
        ts = msprime.sim_ancestry(5, end_time=0.5, random_seed=53)
        self.assertGreater(ts.first().num_roots, 1)
        recapitated1 = msprime.sim_ancestry(initial_state=ts, random_seed=234)
        self.assertEqual(recapitated1.num_trees, 1)
        self.assertEqual(recapitated1.first().num_roots, 1)

        # We should get the same answer from the providing the tables argument.
        recapitated2 = msprime.sim_ancestry(initial_state=ts.tables, random_seed=234)
        self.assertTrue(tree_sequences_equal(recapitated1, recapitated2))


class TestSimulateInterface(unittest.TestCase):
    """
    Some simple test cases for the simulate() interface.
    """

    def test_defaults(self):
        n = 10
        ts = msprime.simulate(n)
        self.assertIsInstance(ts, tskit.TreeSequence)
        self.assertEqual(ts.get_sample_size(), n)
        self.assertEqual(ts.get_num_trees(), 1)
        self.assertEqual(ts.get_num_mutations(), 0)
        self.assertEqual(ts.get_sequence_length(), 1)
        self.assertEqual(len(list(ts.provenances())), 1)

    def test_positional_args_not_allowed(self):
        with self.assertRaises(TypeError):
            msprime.simulate(2, 100)

    def test_discrete_genome_no_mutations(self):
        def run_sim(discrete_genome=None):
            return msprime.simulate(
                10,
                length=2,
                recombination_rate=1,
                discrete_genome=discrete_genome,
                random_seed=2134,
            )

        ts_discrete = run_sim(True)
        self.assertGreater(ts_discrete.num_trees, 1)
        self.assertTrue(has_discrete_genome(ts_discrete))

        ts_continuous = run_sim(False)
        self.assertGreater(ts_continuous.num_trees, 1)
        self.assertFalse(has_discrete_genome(ts_continuous))

        ts_default = run_sim()
        tables_default = ts_default.dump_tables()
        tables_continuous = ts_continuous.dump_tables()
        tables_continuous.provenances.clear()
        tables_default.provenances.clear()
        self.assertEqual(tables_default, tables_continuous)

    def test_discrete_genome_mutations(self):
        def run_sim(discrete_genome=None):
            return msprime.simulate(
                10,
                length=2,
                recombination_rate=1,
                mutation_rate=1,
                discrete_genome=discrete_genome,
                random_seed=2134,
            )

        ts_discrete = run_sim(True)
        self.assertGreater(ts_discrete.num_trees, 1)
        self.assertGreater(ts_discrete.num_sites, 1)
        self.assertTrue(has_discrete_genome(ts_discrete))

        ts_continuous = run_sim(False)
        self.assertGreater(ts_continuous.num_trees, 1)
        self.assertGreater(ts_discrete.num_sites, 1)
        self.assertFalse(has_discrete_genome(ts_continuous))

        ts_default = run_sim()
        tables_default = ts_default.dump_tables()
        tables_continuous = ts_continuous.dump_tables()
        tables_continuous.provenances.clear()
        tables_default.provenances.clear()
        self.assertEqual(tables_default, tables_continuous)

    def test_discrete_genome_migrations(self):
        def run_sim(discrete_genome=None):
            demography = msprime.Demography.stepping_stone_1d(2, 0.1)
            samples = demography.sample(5, 5)
            return msprime.simulate(
                samples=samples,
                demography=demography,
                length=5,
                recombination_rate=1,
                discrete_genome=discrete_genome,
                record_migrations=True,
                random_seed=2134,
            )

        ts_discrete = run_sim(True)
        self.assertGreater(ts_discrete.num_trees, 1)
        self.assertGreater(ts_discrete.num_migrations, 1)
        self.assertTrue(has_discrete_genome(ts_discrete))

        ts_continuous = run_sim(False)
        self.assertGreater(ts_continuous.num_trees, 1)
        self.assertGreater(ts_continuous.num_migrations, 1)
        self.assertFalse(has_discrete_genome(ts_continuous))

    def test_numpy_random_seed(self):
        seed = np.array([12345], dtype=np.int64)[0]
        self.assertEqual(seed.dtype, np.int64)
        ts1 = msprime.simulate(10, random_seed=seed)
        ts2 = msprime.simulate(10, random_seed=seed)
        self.assertEqual(ts1.tables.nodes, ts2.tables.nodes)

    def verify_provenance(self, provenance):
        """
        Checks that the specified provenance object has the right sort of
        properties.
        """
        # Generate the ISO 8601 time for now, without the high precision suffix,
        # and compare the prefixes.
        today = datetime.date.today().isoformat()
        k = len(today)
        self.assertEqual(provenance.timestamp[:k], today)
        self.assertEqual(provenance.timestamp[k], "T")
        d = json.loads(provenance.record)
        self.assertGreater(len(d), 0)

    def test_provenance(self):
        ts = msprime.simulate(10)
        self.assertEqual(ts.num_provenances, 1)
        self.verify_provenance(ts.provenance(0))
        for ts in msprime.simulate(10, num_replicates=10):
            self.assertEqual(ts.num_provenances, 1)
            self.verify_provenance(ts.provenance(0))

    def test_end_time(self):
        ts = msprime.simulate(15, recombination_rate=2, random_seed=8, end_time=0.1)
        for tree in ts.trees():
            for root in tree.roots:
                self.assertEqual(tree.time(root), 0.1)

    def test_replicates(self):
        n = 20
        num_replicates = 10
        count = 0
        for ts in msprime.simulate(n, num_replicates=num_replicates):
            count += 1
            self.assertIsInstance(ts, tskit.TreeSequence)
            self.assertEqual(ts.get_sample_size(), n)
            self.assertEqual(ts.get_num_trees(), 1)
        self.assertEqual(num_replicates, count)

    def test_mutations(self):
        n = 10
        ts = msprime.simulate(n, mutation_rate=10)
        self.assertIsInstance(ts, tskit.TreeSequence)
        self.assertEqual(ts.get_sample_size(), n)
        self.assertEqual(ts.get_num_trees(), 1)
        self.assertGreater(ts.get_num_mutations(), 0)

    def test_no_mutations_with_start_time(self):
        with self.assertRaises(ValueError):
            msprime.simulate(10, mutation_rate=10, start_time=3)
        # But fine if we set start_time = None
        ts = msprime.simulate(10, mutation_rate=10, start_time=None, random_seed=1)
        self.assertGreater(ts.num_sites, 0)

    def test_mutation_generator_unsupported(self):
        with self.assertRaises(ValueError):
            msprime.simulate(10, mutation_generator="some non-None value")

    def test_mutation_interface(self):
        for bad_type in [{}, [], self]:
            self.assertRaises(TypeError, msprime.simulate, 10, mutation_rate=bad_type)
        for bad_value in ["x", "234x"]:
            self.assertRaises(ValueError, msprime.simulate, 10, mutation_rate=bad_value)

    def test_recombination(self):
        n = 10
        ts = msprime.simulate(n, recombination_rate=10)
        self.assertIsInstance(ts, tskit.TreeSequence)
        self.assertEqual(ts.sample_size, n)
        self.assertGreater(ts.num_trees, 1)
        self.assertEqual(ts.num_mutations, 0)

    def test_gene_conversion_simple_map(self):
        n = 10
        ts = msprime.simulate(
            n,
            gene_conversion_rate=1,
            gene_conversion_track_length=1,
            length=10,
            recombination_rate=1,
            discrete_genome=True,
        )
        self.assertIsInstance(ts, tskit.TreeSequence)
        self.assertEqual(ts.num_samples, n)
        self.assertGreater(ts.num_trees, 1)

    def test_gene_conversion_continuous(self):
        with self.assertRaises(ValueError):
            msprime.simulate(
                10,
                gene_conversion_rate=1,
                gene_conversion_track_length=1,
                discrete_genome=False,
            )

    def test_gene_conversion_default_map(self):
        n = 10
        ts = msprime.simulate(
            n,
            length=10,
            gene_conversion_rate=1,
            gene_conversion_track_length=1,
            discrete_genome=True,
        )
        self.assertIsInstance(ts, tskit.TreeSequence)
        self.assertEqual(ts.num_samples, n)
        self.assertGreater(ts.num_trees, 1)

    def test_num_labels(self):
        # Running simulations with different numbers of labels in the default
        # setting should have no effect.
        tables = [
            msprime.simulate(10, num_labels=num_labels, random_seed=1).tables
            for num_labels in range(1, 5)
        ]
        for t in tables:
            t.provenances.clear()
        for t in tables:
            self.assertEqual(t, tables[0])

    def test_replicate_index(self):
        tables_1 = list(msprime.simulate(10, num_replicates=5, random_seed=1))[4].tables
        tables_2 = msprime.simulate(10, replicate_index=4, random_seed=1).tables
        tables_1.provenances.clear()
        tables_2.provenances.clear()
        self.assertEqual(tables_1, tables_2)

        with self.assertRaises(ValueError) as cm:
            msprime.simulate(5, replicate_index=5)
        self.assertEqual(
            "Cannot specify replicate_index without random_seed as this "
            "has the same effect as not specifying replicate_index i.e. a "
            "random tree sequence",
            str(cm.exception),
        )
        with self.assertRaises(ValueError) as cm:
            msprime.simulate(5, random_seed=1, replicate_index=5, num_replicates=26)
        self.assertEqual(
            "Cannot specify replicate_index with num_replicates as only "
            "the replicate_index specified will be returned.",
            str(cm.exception),
        )


class TestReprRoundTrip(unittest.TestCase):
    """
    Tests that we can eval the repr of objects to round trip them.
    """

    def assert_repr_round_trip(self, obj_list):
        for obj in obj_list:
            obj_copy = eval(repr(obj), globals(), msprime.__dict__)
            self.assertEqual(obj_copy, obj)
            self.assertFalse(obj_copy is obj)

    def test_population(self):
        examples = [
            msprime.Population(),
            msprime.Population(initial_size=2),
            msprime.Population(growth_rate=5),
            msprime.Population(initial_size=234, growth_rate=10),
        ]
        self.assert_repr_round_trip(examples)

    def test_population_parameters_change(self):
        examples = [
            msprime.PopulationParametersChange(time=1, initial_size=1),
            msprime.PopulationParametersChange(time=1, growth_rate=2),
            msprime.PopulationParametersChange(time=1, growth_rate=1, population=2),
            msprime.PopulationParametersChange(
                time=3, initial_size=3, growth_rate=1, population=2
            ),
        ]
        self.assert_repr_round_trip(examples)

    def test_migration_rate_change(self):
        examples = [
            msprime.MigrationRateChange(time=1, rate=1),
            msprime.MigrationRateChange(time=1, rate=1, source=1, dest=2),
        ]
        self.assert_repr_round_trip(examples)

    def test_mass_migration(self):
        examples = [
            msprime.MassMigration(time=1, source=1, dest=2),
            msprime.MassMigration(time=1, source=1, dest=2, proportion=0.2),
        ]
        self.assert_repr_round_trip(examples)

    def test_simulation_model_change(self):
        examples = [
            msprime.SimulationModelChange(),
            msprime.SimulationModelChange(model="hudson"),
            msprime.SimulationModelChange(model=msprime.DiscreteTimeWrightFisher()),
            msprime.SimulationModelChange(
                model=msprime.BetaCoalescent(alpha=1, truncation_point=2)
            ),
        ]
        self.assert_repr_round_trip(examples)

    def test_simple_bottleneck(self):
        examples = [
            msprime.SimpleBottleneck(time=10, population=2),
            msprime.SimpleBottleneck(time=10, population=2, proportion=0.5),
        ]
        self.assert_repr_round_trip(examples)

    def test_instantaneous_bottleneck(self):
        examples = [
            msprime.InstantaneousBottleneck(time=10, population=1),
            msprime.InstantaneousBottleneck(time=10, population=1, strength=10),
        ]
        self.assert_repr_round_trip(examples)

    def test_census_event(self):
        examples = [
            msprime.CensusEvent(time=10),
        ]
        self.assert_repr_round_trip(examples)

    def test_simulation_models(self):
        examples = [
            msprime.StandardCoalescent(),
            msprime.SmcApproxCoalescent(),
            msprime.SmcPrimeApproxCoalescent(),
            msprime.DiscreteTimeWrightFisher(),
            msprime.WrightFisherPedigree(),
            msprime.BetaCoalescent(),
            msprime.BetaCoalescent(alpha=1, truncation_point=10),
            msprime.DiracCoalescent(),
            msprime.DiracCoalescent(psi=1234, c=56),
            msprime.SweepGenicSelection(
                position=1, start_frequency=0.5, end_frequency=0.9, alpha=1, dt=1e-4
            ),
        ]
        self.assert_repr_round_trip(examples)
