#   Copyright 2017 ProjectQ-Framework (www.projectq.ch)
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""
Tests for projectq.backends._sim._simulator.py, using both the Python
and the C++ simulator as backends.
"""

import copy
import math
import cmath
import numpy
import pytest
import random
import scipy
import scipy.sparse
import scipy.sparse.linalg

from projectq import MainEngine
from projectq.cengines import (BasicEngine, BasicMapperEngine, DummyEngine,
                               LocalOptimizer, NotYetMeasuredError)
from projectq.ops import (All, Allocate, BasicGate, BasicMathGate, CNOT, C,
                          Command, H, Measure, QubitOperator, Rx, Ry, Rz, S,
                          TimeEvolution, Toffoli, X, Y, Z, Swap, SqrtSwap)
from projectq.libs.math import (AddConstant,
                                AddConstantModN,
                                SubConstant,
                                SubConstantModN,
                                MultiplyByConstantModN,
                                DivideByConstantModN)
from projectq.meta import Control, Dagger, LogicalQubitIDTag
from projectq.types import WeakQubitRef

from projectq.backends import Simulator


def test_is_qrack_simulator_present():
    _qracksim = pytest.importorskip("projectq.backends._qracksim._qracksim")
    import projectq.backends._qracksim._qracksim as _


def get_available_simulators():
    result = []
    try:
        test_is_qrack_simulator_present()
        result.append("qrack_simulator_qengine")
        result.append("qrack_simulator_qunit")
    except:
        pass
    return result


@pytest.fixture(params=get_available_simulators())
def sim(request):
    if request.param == "qrack_simulator_qengine":
        from projectq.backends._qracksim._qracksim import QrackSimulator as QrackSim
        sim = Simulator()
        sim._simulator = QrackSim(1, -1, 1)
    elif request.param == "qrack_simulator_qunit":
        from projectq.backends._qracksim._qracksim import QrackSimulator as QrackSim
        sim = Simulator()
        sim._simulator = QrackSim(1, -1, 2)
    return sim


@pytest.fixture(params=["mapper", "no_mapper"])
def mapper(request):
    """
    Adds a mapper which changes qubit ids by adding 1
    """
    if request.param == "mapper":

        class TrivialMapper(BasicMapperEngine):
            def __init__(self):
                BasicEngine.__init__(self)
                self.current_mapping = dict()

            def receive(self, command_list):
                for cmd in command_list:
                    for qureg in cmd.all_qubits:
                        for qubit in qureg:
                            if qubit.id == -1:
                                continue
                            elif qubit.id not in self.current_mapping:
                                previous_map = self.current_mapping
                                previous_map[qubit.id] = qubit.id + 1
                                self.current_mapping = previous_map
                    self._send_cmd_with_mapped_ids(cmd)

        return TrivialMapper()
    if request.param == "no_mapper":
        return None


class Mock1QubitGate(BasicGate):
        def __init__(self):
            BasicGate.__init__(self)
            self.cnt = 0

        @property
        def matrix(self):
            self.cnt += 1
            return numpy.matrix([[0, 1],
                                 [1, 0]])


class Mock6QubitGate(BasicGate):
        def __init__(self):
            BasicGate.__init__(self)
            self.cnt = 0

        @property
        def matrix(self):
            self.cnt += 1
            return numpy.eye(2 ** 6)


class MockNoMatrixGate(BasicGate):
        def __init__(self):
            BasicGate.__init__(self)
            self.cnt = 0

        @property
        def matrix(self):
            self.cnt += 1
            raise AttributeError


def test_simulator_is_available(sim):
    backend = DummyEngine(save_commands=True)
    eng = MainEngine(backend, [])
    qubit = eng.allocate_qubit()
    Measure | qubit
    qubit[0].__del__()
    assert len(backend.received_commands) == 3

    # Test that allocate, measure, basic math, and deallocate are available.
    for cmd in backend.received_commands:
        assert sim.is_available(cmd)

    new_cmd = backend.received_commands[-1]

    new_cmd.gate = Mock6QubitGate()
    assert not sim.is_available(new_cmd)

    new_cmd.gate = MockNoMatrixGate()
    assert not sim.is_available(new_cmd)

    new_cmd.gate = Mock1QubitGate()
    assert sim.is_available(new_cmd)

    new_cmd = backend.received_commands[-2]
    assert len(new_cmd.qubits) == 1

    new_cmd.gate = AddConstantModN(1, 2)
    assert sim.is_available(new_cmd)

    new_cmd.gate = MultiplyByConstantModN(1, 2)
    assert sim.is_available(new_cmd)

    new_cmd.gate = DivideByConstantModN(1, 2)
    assert sim.is_available(new_cmd)


def test_simulator_cheat(sim):
    # cheat function should return a tuple
    assert isinstance(sim.cheat(), tuple)
    # first entry is the qubit mapping.
    # should be empty:
    assert len(sim.cheat()[0]) == 0
    # state vector should only have 1 entry:
    assert len(sim.cheat()[1]) == 1

    eng = MainEngine(sim, [])
    qubit = eng.allocate_qubit()

    # one qubit has been allocated
    assert len(sim.cheat()[0]) == 1
    assert sim.cheat()[0][0] == 0
    assert len(sim.cheat()[1]) == 2
    assert 1. == pytest.approx(abs(sim.cheat()[1][0]))

    qubit[0].__del__()
    # should be empty:
    assert len(sim.cheat()[0]) == 0
    # state vector should only have 1 entry:
    assert len(sim.cheat()[1]) == 1


def test_simulator_functional_measurement(sim):
    eng = MainEngine(sim, [])
    qubits = eng.allocate_qureg(5)
    # entangle all qubits:
    H | qubits[0]
    for qb in qubits[1:]:
        CNOT | (qubits[0], qb)

    All(Measure) | qubits

    bit_value_sum = sum([int(qubit) for qubit in qubits])
    assert bit_value_sum == 0 or bit_value_sum == 5


def test_simulator_measure_mapped_qubit(sim):
    eng = MainEngine(sim, [])
    qb1 = WeakQubitRef(engine=eng, idx=1)
    qb2 = WeakQubitRef(engine=eng, idx=2)
    cmd0 = Command(engine=eng, gate=Allocate, qubits=([qb1],))
    cmd1 = Command(engine=eng, gate=X, qubits=([qb1],))
    cmd2 = Command(engine=eng, gate=Measure, qubits=([qb1],), controls=[],
                   tags=[LogicalQubitIDTag(2)])
    with pytest.raises(NotYetMeasuredError):
        int(qb1)
    with pytest.raises(NotYetMeasuredError):
        int(qb2)
    eng.send([cmd0, cmd1, cmd2])
    eng.flush()
    with pytest.raises(NotYetMeasuredError):
        int(qb1)
    assert int(qb2) == 1



def test_simulator_kqubit_exception(sim):
    m1 = Rx(0.3).matrix
    m2 = Rx(0.8).matrix
    m3 = Ry(0.1).matrix
    m4 = Rz(0.9).matrix.dot(Ry(-0.1).matrix)
    m = numpy.kron(m4, numpy.kron(m3, numpy.kron(m2, m1)))

    class KQubitGate(BasicGate):
        @property
        def matrix(self):
            return m

    eng = MainEngine(sim, [])
    qureg = eng.allocate_qureg(3)
    with pytest.raises(Exception):
        KQubitGate() | qureg
    with pytest.raises(Exception):
        H | qureg

def test_simulator_swap(sim):
    eng = MainEngine(sim, [])
    qubits1 = eng.allocate_qureg(1)
    qubits2 = eng.allocate_qureg(1)

    X | qubits1

    Swap | (qubits1, qubits2)
    All(Measure) | qubits1
    All(Measure) | qubits2
    assert (int(qubits1[0]) == 0) and (int(qubits2[0]) == 1)

    SqrtSwap | (qubits1, qubits2)
    SqrtSwap | (qubits1, qubits2)
    All(Measure) | qubits1
    All(Measure) | qubits2
    assert (int(qubits1[0]) == 1) and (int(qubits2[0]) == 0)
    

def test_simulator_math(sim):
    eng = MainEngine(sim, [])
    qubits = eng.allocate_qureg(8)

    AddConstant(1) | qubits;
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 1

    AddConstantModN(10, 256) | qubits;
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 11

    controls = eng.allocate_qureg(1)

    # Control is off
    C(AddConstantModN(10, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 11

    # Turn control on
    X | controls

    C(AddConstantModN(10, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 21

    SubConstant(5) | qubits;
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 16

    C(SubConstantModN(10, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 6

    # Turn control off
    X | controls

    C(SubConstantModN(10, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 6

    MultiplyByConstantModN(2, 256) | qubits;
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 12

    DivideByConstantModN(3, 256) | qubits;
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 4

    # Control is off

    C(MultiplyByConstantModN(2, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 4

    C(DivideByConstantModN(3, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 4

    # Turn control on
    X | controls

    C(MultiplyByConstantModN(10, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 40

    C(DivideByConstantModN(5, 256)) | (controls, qubits)
    All(Measure) | qubits
    value = 0
    for i in range(len(qubits)):
        value += int(qubits[i]) << i
    assert value == 8


def test_simulator_probability(sim, mapper):
    engine_list = [LocalOptimizer()]
    if mapper is not None:
        engine_list.append(mapper)
    eng = MainEngine(sim, engine_list=engine_list)
    qubits = eng.allocate_qureg(6)
    All(H) | qubits
    eng.flush()
    bits = [0, 0, 1, 0, 1, 0]
    for i in range(6):
        assert (eng.backend.get_probability(bits[:i], qubits[:i]) ==
                pytest.approx(0.5**i))
    extra_qubit = eng.allocate_qubit()
    with pytest.raises(RuntimeError):
        eng.backend.get_probability([0], extra_qubit)
    del extra_qubit
    All(H) | qubits
    Ry(2 * math.acos(math.sqrt(0.3))) | qubits[0]
    eng.flush()
    assert eng.backend.get_probability([0], [qubits[0]]) == pytest.approx(0.3)
    Ry(2 * math.acos(math.sqrt(0.4))) | qubits[2]
    eng.flush()
    assert eng.backend.get_probability([0], [qubits[2]]) == pytest.approx(0.4)
    assert (eng.backend.get_probability([0, 0], qubits[:3:2]) ==
            pytest.approx(0.12))
    assert (eng.backend.get_probability([0, 1], qubits[:3:2]) ==
            pytest.approx(0.18))
    assert (eng.backend.get_probability([1, 0], qubits[:3:2]) ==
            pytest.approx(0.28))
    All(Measure) | qubits


def test_simulator_amplitude(sim, mapper):
    # Qrack adds a random global phase factor to all initial states,
    # (because the global phase is physically unknowable and has no measurable effect)
    engine_list = [LocalOptimizer()]
    if mapper is not None:
        engine_list.append(mapper)
    eng = MainEngine(sim, engine_list=engine_list)
    qubits = eng.allocate_qureg(6)
    All(X) | qubits
    All(H) | qubits
    eng.flush()
    bits = [0, 0, 1, 0, 1, 0]
    polR, polPhi = cmath.polar(eng.backend.get_amplitude(bits, qubits))
    while polPhi < 0:
        polPhi += 2 * math.pi
    assert polR == pytest.approx(1. / 8.)
    bits = [0, 0, 0, 0, 1, 0]
    polR2, polPhi2 = cmath.polar(eng.backend.get_amplitude(bits, qubits))
    while polPhi2 < math.pi:
        polPhi2 += 2 * math.pi
    assert polR2 == pytest.approx(polR)
    assert (polPhi2 - math.pi) == pytest.approx(polPhi)
    bits = [0, 1, 1, 0, 1, 0]
    polR3, polPhi3 = cmath.polar(eng.backend.get_amplitude(bits, qubits))
    while polPhi3 < math.pi:
        polPhi3 += 2 * math.pi
    assert polR3 == pytest.approx(polR)
    assert (polPhi3 - math.pi) == pytest.approx(polPhi)
    All(H) | qubits
    All(X) | qubits
    Ry(2 * math.acos(0.3)) | qubits[0]
    eng.flush()
    bits = [0] * 6
    polR, polPhi = cmath.polar(eng.backend.get_amplitude(bits, qubits))
    assert polR == pytest.approx(0.3)
    bits[0] = 1
    polR, polPhi = cmath.polar(eng.backend.get_amplitude(bits, qubits))
    assert (polR ==
            pytest.approx(math.sqrt(0.91)))
    All(Measure) | qubits
    # raises if not all qubits are in the list:
    with pytest.raises(RuntimeError):
        eng.backend.get_amplitude(bits, qubits[:-1])
    # doesn't just check for length:
    with pytest.raises(RuntimeError):
        eng.backend.get_amplitude(bits, qubits[:-1] + [qubits[0]])
    extra_qubit = eng.allocate_qubit()
    eng.flush()
    # there is a new qubit now!
    with pytest.raises(RuntimeError):
        eng.backend.get_amplitude(bits, qubits)


def test_simulator_set_wavefunction(sim, mapper):
    engine_list = [LocalOptimizer()]
    if mapper is not None:
        engine_list.append(mapper)
    eng = MainEngine(sim, engine_list=engine_list)
    qubits = eng.allocate_qureg(2)
    wf = [0., 0., math.sqrt(0.2), math.sqrt(0.8)]
    with pytest.raises(RuntimeError):
        eng.backend.set_wavefunction(wf, qubits)
    eng.flush()
    eng.backend.set_wavefunction(wf, qubits)
    assert pytest.approx(eng.backend.get_probability('1', [qubits[0]])) == .8
    assert pytest.approx(eng.backend.get_probability('01', qubits)) == .2
    assert pytest.approx(eng.backend.get_probability('1', [qubits[1]])) == 1.
    All(Measure) | qubits


def test_simulator_set_wavefunction_always_complex(sim):
    """ Checks that wavefunction is always complex """
    eng = MainEngine(sim)
    qubit = eng.allocate_qubit()
    eng.flush()
    wf = [1., 0]
    eng.backend.set_wavefunction(wf, qubit)
    Y | qubit
    eng.flush()
    amplitude = eng.backend.get_amplitude('1', qubit)
    assert amplitude == pytest.approx(1j) or amplitude == pytest.approx(-1j)


def test_simulator_collapse_wavefunction(sim, mapper):
    engine_list = [LocalOptimizer()]
    if mapper is not None:
        engine_list.append(mapper)
    eng = MainEngine(sim, engine_list=engine_list)
    qubits = eng.allocate_qureg(4)
    # unknown qubits: raises
    with pytest.raises(RuntimeError):
        eng.backend.collapse_wavefunction(qubits, [0] * 4)
    eng.flush()
    eng.backend.collapse_wavefunction(qubits, [0] * 4)
    assert pytest.approx(eng.backend.get_probability([0] * 4, qubits)) == 1.
    All(H) | qubits[1:]
    eng.flush()
    assert pytest.approx(eng.backend.get_probability([0] * 4, qubits)) == .125
    # impossible outcome: raises
    with pytest.raises(RuntimeError):
        eng.backend.collapse_wavefunction(qubits, [1] + [0] * 3)
    eng.backend.collapse_wavefunction(qubits[:-1], [0, 1, 0])
    probability = eng.backend.get_probability([0, 1, 0, 1], qubits)
    assert probability == pytest.approx(.5)
    eng.backend.set_wavefunction([1.] + [0.] * 15, qubits)
    H | qubits[0]
    CNOT | (qubits[0], qubits[1])
    eng.flush()
    eng.backend.collapse_wavefunction([qubits[0]], [1])
    probability = eng.backend.get_probability([1, 1], qubits[0:2])
    assert probability == pytest.approx(1.)


def test_simulator_no_uncompute_exception(sim):
    eng = MainEngine(sim, [])
    qubit = eng.allocate_qubit()
    H | qubit
    with pytest.raises(RuntimeError):
        qubit[0].__del__()
    # If you wanted to keep using the qubit, you shouldn't have deleted it.
    assert qubit[0].id == -1


def test_simulator_functional_entangle(sim):
    eng = MainEngine(sim, [])
    qubits = eng.allocate_qureg(5)
    # entangle all qubits:
    H | qubits[0]
    for qb in qubits[1:]:
        CNOT | (qubits[0], qb)

    # check the state vector:
    assert .5 == pytest.approx(abs(sim.cheat()[1][0])**2)
    assert .5 == pytest.approx(abs(sim.cheat()[1][31])**2)
    for i in range(1, 31):
        assert 0. == pytest.approx(abs(sim.cheat()[1][i]))

    # unentangle all except the first 2
    for qb in qubits[2:]:
        CNOT | (qubits[0], qb)

    # entangle using Toffolis
    for qb in qubits[2:]:
        Toffoli | (qubits[0], qubits[1], qb)

    # check the state vector:
    assert .5 == pytest.approx(abs(sim.cheat()[1][0])**2)
    assert .5 == pytest.approx(abs(sim.cheat()[1][31])**2)
    for i in range(1, 31):
        assert 0. == pytest.approx(abs(sim.cheat()[1][i]))

    # uncompute using multi-controlled NOTs
    with Control(eng, qubits[0:-1]):
        X | qubits[-1]
    with Control(eng, qubits[0:-2]):
        X | qubits[-2]
    with Control(eng, qubits[0:-3]):
        X | qubits[-3]
    CNOT | (qubits[0], qubits[1])
    H | qubits[0]

    # check the state vector:
    assert 1. == pytest.approx(abs(sim.cheat()[1][0])**2)
    for i in range(1, 32):
        assert 0. == pytest.approx(abs(sim.cheat()[1][i]))

    All(Measure) | qubits


def test_simulator_convert_logical_to_mapped_qubits(sim):
    mapper = BasicMapperEngine()

    def receive(command_list):
        pass

    mapper.receive = receive
    eng = MainEngine(sim, [mapper])
    qubit0 = eng.allocate_qubit()
    qubit1 = eng.allocate_qubit()
    mapper.current_mapping = {qubit0[0].id: qubit1[0].id,
                              qubit1[0].id: qubit0[0].id}
    assert (sim._convert_logical_to_mapped_qureg(qubit0 + qubit1) ==
            qubit1 + qubit0)
