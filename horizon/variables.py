import copy
from typing import List
import casadi as cs
from collections import OrderedDict
import logging
import numpy as np
import pickle
import horizon.misc_function as misc
import pprint
from abc import ABC, abstractmethod
'''
now the StateVariable is only abstract at the very beginning.
Formerly
'''
# todo create function checker to check if nodes are in self.nodes and if everything is ok with the input (no dict, no letters...)

class AbstractVariable(ABC, cs.SX):
    """
    Abstract Variable of Horizon Problem.

    Notes:
          Horizon allows the user to work only with abstract variables. Internally, these variables are projected over the horizon nodes.
    """
    def __init__(self, tag: str, dim: int):
        """
        Initialize the Abstract Variable. Inherits from the symbolic CASADI varaible SX.

        Args:
            tag: name of the variable
            dim: dimension of the variable
        """
        super(AbstractVariable, self).__init__(cs.SX.sym(tag, dim))

        self._tag = tag
        self._dim = dim
        # offset of a variable is used to point to the desired previous/next implemented
        # Example:
            # offset 1 of variable x --> refers to implemented variable x at the next node
        self._offset = 0

    def getDim(self) -> int:
        """
        Getter for the dimension of the abstract variable.

        Returns:
            dimension of the variable
        """
        return self._dim

    @abstractmethod
    def getName(self):
        ...
        # return self.tag

    def getOffset(self):
        return self._offset

class OffsetVariable(AbstractVariable):
    def __init__(self, parent_name, tag, dim, nodes, offset, var_impl):
        """
        Initialize the Offset Variable.

        Args:
            tag: name of the variable
            dim: dimension of the variable
            nodes: nodes the variable is defined on
            offset: offset of the variable (which (previous/next) node it refers to
            var_impl: implemented variables it refers to (of base class Variable)
        """
        super(OffsetVariable, self).__init__(tag, dim)

        self.parent_name = parent_name
        self._nodes = list()
        self._offset = offset
        self._var_impl = var_impl

        for node in self._var_impl.keys():
            self._nodes.append(int(node.split("n", 1)[1]))

    def getImpl(self, nodes=None):
        """
        Getter for the implemented offset variable.

        Args:
            node: node at which the variable is retrieved

        Returns:
            implemented instances of the abstract offsetted variable
        """

        # todo is this nice? to update nodes given the _var_impl
        #   another possibility is sharing also the _nodes
        self._nodes.clear()
        for node in self._var_impl.keys():
            self._nodes.append(int(node.split("n", 1)[1]))

        if nodes is None:
            nodes = self._nodes

        # offset the node of self.offset
        offset_nodes = [node + self._offset for node in nodes]
        offset_nodes = misc.checkNodes(offset_nodes, self._nodes)

        var_impl = cs.vertcat(*[self._var_impl['n' + str(i)]['var'] for i in offset_nodes])

        return var_impl

    def getName(self):
        """
        Get name of the variable. Warning: not always same as the tag

        Returns:
            name of the variable

        """
        return self.parent_name

    def getNodes(self):
        """
        Getter for the active nodes.

        Returns:
            list of active nodes
        """
        return self._nodes

class SingleParameter(AbstractVariable):
    """
    Single Parameter of Horizon Problem.
    It is used for parametric problems: it is a symbolic variable in the optimization problem but it is not optimized. Rather, it is kept parametric and can be assigned before solving the problem.
    Parameters are specified before building the problem and can be 'assigned' afterwards, before solving the problem.
    The assigned value is the same along the horizon, since this parameter is node-independent.
    The Parameter is abstract, and gets implemented automatically.
    """
    def __init__(self, tag, dim, dummy_nodes):
        """
        Initialize the Single Parameter: a node-independent parameter which is not projected over the horizon.

        Args:
            tag: name of the parameter
            dim: dimension of the parameter
            dummy_nodes: useless input, used to simplify the framework mechanics
        """
        super(SingleParameter, self).__init__(tag, dim)

        self._par_impl = dict()
        self._par_impl['par'] = cs.SX.sym(self._tag + '_impl', self._dim)
        self._par_impl['val'] = np.zeros(self._dim)

    def assign(self, vals):
        """
        Assign a value to the parameter. Can be assigned also after the problem is built, before solving the problem.
        If not assigned, its default value is zero.

        Args:
            vals: value of the parameter
        """
        vals = misc.checkValueEntry(vals)

        # todo what if vals has no shape?
        if vals.shape[0] != self._dim:
            raise Exception('Wrong dimension of parameter values inserted.')

        self._par_impl['val'] = vals

    def getImpl(self, nodes=None):
        """
        Getter for the implemented parameter. Node is useless, since this parameter is node-independent.

        Args:
            node: useless input, used to simplify the framework mechanics

        Returns:
            instance of the implemented parameter
        """
        if nodes is None:
            par_impl = self._par_impl['par']
        else:
            nodes = misc.checkNodes(nodes)
            par_impl = cs.vertcat(*[self._par_impl['par'] for i in nodes])
        return par_impl

    def getNodes(self):
        """
        Getter for the active nodes.

        Returns:
            -1 since this parameter is node-independent
        """
        # todo what if I return all the nodes?
        return [-1]

    def getValues(self, nodes=None):
        """
        Getter for the value assigned to the parameter. It is the same throughout all the nodes, since this parameter is node-independent.

        Args:
            dummy_node: useless input, used to simplify the framework mechanics

        Returns:
            value assigned to the parameter
        """
        if nodes is None:
            par_impl = self._par_impl['val']
        else:
            nodes = misc.checkNodes(nodes)
            par_impl = cs.vertcat(*[self._par_impl['val'] for i in nodes])
        return par_impl

    def getName(self):
        """
        getter for the name of the parameter

        Returns:
            name of the parameter
        """
        return self._tag

class Parameter(AbstractVariable):
    """
    Parameter of Horizon Problem.
    It is used for parametric problems: it is a symbolic variable in the optimization problem but it is not optimized. Rather, it is kept parametric and can be assigned before solving the problem.
    """
    def __init__(self, tag, dim, nodes):
        """
        Initialize the Parameter: an abstract parameter projected over the horizon.
        Parameters are specified before building the problem and can be 'assigned' afterwards, before solving the problem.
        The assigned value can vary along the horizon, since this parameter is node-dependent.
        The Parameter is abstract, and gets implemented automatically.

        Args:
            tag: name of the parameter
            dim: dimension of the parameter
            nodes: nodes the parameter is implemented at
        """
        super(Parameter, self).__init__(tag, dim)

        self._par_impl = dict()

        self._nodes = nodes
        self._project()

    def _project(self):
        """
        Implements the parameter along the horizon nodes.
        Generates an ordered dictionary containing the implemented parameter and its value at each node {node: {par, val}}
        """
        new_par_impl = OrderedDict()

        for n in self._nodes:
            if 'n' + str(n) in self._par_impl:
                new_par_impl['n' + str(n)] = self._par_impl_dict['n' + str(n)]
            else:
                par_impl = cs.SX.sym(self._tag + '_' + str(n), self._dim)
                new_par_impl['n' + str(n)] = dict()
                new_par_impl['n' + str(n)]['par'] = par_impl
                new_par_impl['n' + str(n)]['val'] = np.zeros(self._dim)

        self._par_impl = new_par_impl

    def getNodes(self):
        """
        Getter for the nodes of the parameters.

        Returns:
            list of nodes the parameter is active on.
        """
        return self._nodes

    def assign(self, vals, nodes=None):
        """
       Assign a value to the parameter at a desired node. Can be assigned also after the problem is built, before solving the problem.
       If not assigned, its default value is zero.

       Args:
           vals: value of the parameter
           nodes: nodes at which the parameter is assigned
       """
        if nodes is None:
            nodes = self._nodes
        else:
            nodes = misc.checkNodes(nodes, self._nodes)

        vals = misc.checkValueEntry(vals)

        if vals.shape[0] != self._dim:
            raise Exception('Wrong dimension of parameter values inserted.')

        for node in nodes:
            self._par_impl['n' + str(node)]['val'] = vals

    def getImpl(self, nodes=None):
        """
        Getter for the implemented parameter.

        Args:
            node: node at which the parameter is retrieved. If not specified, this function returns an SX array with all the implemented parameters along the nodes.

        Returns:
            implemented instances of the abstract parameter
        """

        if nodes is None:
            nodes = self._nodes

        nodes = misc.checkNodes(nodes, self._nodes)

        par_impl = cs.vertcat(*[self._par_impl['n' + str(i)]['par'] for i in nodes])

        return par_impl


    def getValues(self, nodes=None):
        """
        Getter for the value of the parameter.

        Args:
            node: node at which the value of the parameter is retrieved. If not specified, this function returns a matrix with all the values of the parameter along the nodes.

        Returns:
            value/s of the parameter
        """
        if nodes is None:
            nodes = self._nodes

        nodes = misc.checkNodes(nodes, self._nodes)

        par_impl = cs.vertcat(*[self._par_impl['n' + str(i)]['val'] for i in nodes])

        return par_impl

    def getName(self):
        """
        getter for the name of the parameter

        Returns:
            name of the parameter
        """
        return self._tag

    def __reduce__(self):
        """
        Experimental function to serialize this element.

        Returns:
            instance of this element serialized
        """
        return (self.__class__, (self._tag, self._dim, self._nodes,))

class SingleVariable(AbstractVariable):
    """
    Single Variable of Horizon Problem: generic variable of the optimization problem.
    The single variable is the same along the horizon, since it is node-independent.
    The Variable is abstract, and gets implemented automatically.
    """
    def __init__(self, tag, dim, dummy_nodes):
        """
        Initialize the Single Variable: a node-independent variable which is not projected over the horizon.
        The bounds of the variable are initialized to -inf/inf.

        Args:
            tag: name of the variable
            dim: dimension of the variable
            dummy_nodes: useless input, used to simplify the framework mechanics
        """
        super(SingleVariable, self).__init__(tag, dim)

        self._var_impl = dict()
        # todo do i create another var or do I use the SX var inside SingleVariable?
        self._var_impl['var'] = cs.SX.sym(self._tag + '_impl', self._dim)
        self._var_impl['lb'] = np.full(self._dim, -np.inf)
        self._var_impl['ub'] = np.full(self._dim, np.inf)
        self._var_impl['w0'] = np.zeros(self._dim)

    def setLowerBounds(self, bounds):
        """
        Setter for the lower bounds of the variable.

        Args:
            bounds: value of the lower bounds
        """
        bounds = misc.checkValueEntry(bounds)

        if bounds.shape[0] != self._dim:
            raise Exception('Wrong dimension of lower bounds inserted.')

        self._var_impl['lb'] = bounds

    def setUpperBounds(self, bounds):
        """
        Setter for the upper bounds of the variable.

        Args:
            bounds: value of the upper bounds
        """
        bounds = misc.checkValueEntry(bounds)

        if bounds.shape[0] != self._dim:
            raise Exception('Wrong dimension of upper bounds inserted.')

        self._var_impl['ub'] = bounds

    def setBounds(self, lb, ub):
        """
        Setter for the bounds of the variable.

        Args:
            lb: value of the lower bounds
            ub: value of the upper bounds
        """
        self.setLowerBounds(lb)
        self.setUpperBounds(ub)

    def setInitialGuess(self, val):
        """
        Setter for the initial guess of the variable.

        Args:
            val: value of the initial guess
        """
        val = misc.checkValueEntry(val)

        if val.shape[0] != self._dim:
            raise Exception('Wrong dimension of initial guess inserted.')

        self._var_impl['w0'] = val

    def getImpl(self, nodes=None):
        """
        Getter for the implemented variable. Node is useless, since this variable is node-independent.

        Args:
            dummy_node: useless input, used to simplify the framework mechanics

        Returns:
            implemented instances of the abstract variable
        """
        if nodes is None:
            var_impl = self._var_impl['var']
        else:
            nodes = misc.checkNodes(nodes)
            var_impl = cs.vertcat(*[self._var_impl['var'] for i in nodes])
        return var_impl


    def _getVals(self, val_type, nodes):
        """
        wrapper function to get the desired argument from the variable.

        Args:
            val_type: type of the argument to retrieve
            dummy_node: if None, returns an array of the desired argument

        Returns:
            value/s of the desired argument
        """
        if nodes is None:
            var_impl = self._var_impl[val_type]
        else:
            nodes = misc.checkNodes(nodes)
            var_impl = cs.vertcat(*[self._var_impl[val_type] for i in nodes])
        return var_impl

    def getLowerBounds(self, dummy_node=None):
        """
        Getter for the lower bounds of the variable.

        Args:
            node: useless input, used to simplify the framework mechanics

        Returns:
            values of the lower bounds

        """
        return self._getVals('lb', dummy_node)

    def getUpperBounds(self, dummy_node=None):
        """
        Getter for the upper bounds of the variable.

        Args:
            node: useless input, used to simplify the framework mechanics

        Returns:
            values of the upper bounds

        """
        return self._getVals('ub', dummy_node)

    def getBounds(self, dummy_node=None):
        """
        Getter for the bounds of the variable.

        Args:
            node: useless input, used to simplify the framework mechanics

        Returns:
            values of the bounds

        """
        return self.getLowerBounds(dummy_node), self.getUpperBounds(dummy_node)

    def getInitialGuess(self, dummy_node=None):
        """
        Getter for the initial guess of the variable.

        Args:
            node: useless input, used to simplify the framework mechanics

        Returns:
            values of the initial guess
        """
        return self._getVals('w0', dummy_node)

    def getNodes(self):
        """
        Getter for the active nodes of the variable.

        Returns:
            -1 since this parameter is node-independent
        """
        # todo what if I return all the nodes?
        return [-1]

    def getVarOffsetDict(self):
        """
        Getter for the offset variables. Useless, since this variable is node-independent.

        Returns:
            empty dict
        """
        return dict()

    def getImplDim(self):
        """
        Getter for the dimension of the implemented variables.

        Returns:
            dimension of the variable
        """
        return self.shape[0]

    def getName(self):
        """
        getter for the name of the variable

        Returns:
            name of the variable
        """
        return self._tag

class Variable(AbstractVariable):
    """
    Variable of Horizon Problem: generic variable of the optimization problem.
    The Variable is abstract, and gets implemented automatically over the horizon nodes.

    Examples:
        Abstract variable "x". Horizon nodes are N.

        Implemented variable "x" --> x_0, x_1, ... x_N-1, x_N
    """
    def __init__(self, tag, dim, nodes):
        """
        Initialize the Variable.
        The bounds of the variable are initialized to -inf/inf.

        Args:
            tag: name of the variable
            dim: dimension of the variable
            nodes: nodes the variable is defined on
        """
        super(Variable, self).__init__(tag, dim)

        if isinstance(nodes, list):
            nodes.sort()

        self._nodes = nodes

        self.var_offset = dict()
        self._var_impl = dict()

        # i project the variable over the optimization nodes
        self._project()

    def setLowerBounds(self, bounds, nodes=None):
        """
        Setter for the lower bounds of the variable.

        Args:
            bounds: desired bounds of the variable
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        if nodes is None:
            nodes = self._nodes
        else:
            nodes = misc.checkNodes(nodes, self._nodes)

        bounds = misc.checkValueEntry(bounds)
        if bounds.shape[0] != self._dim:
            raise Exception('Wrong dimension of lower bounds inserted.')

        for node in nodes:
            self._var_impl['n' + str(node)]['lb'] = bounds

    def setUpperBounds(self, bounds, nodes=None):
        """
        Setter for the upper bounds of the variable.

        Args:
            bounds: desired bounds of the variable
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        if nodes is None:
            nodes = self._nodes
        else:
            nodes = misc.checkNodes(nodes, self._nodes)

        bounds = misc.checkValueEntry(bounds)

        if bounds.shape[0] != self._dim:
            raise Exception('Wrong dimension of upper bounds inserted.')

        for node in nodes:
            self._var_impl['n' + str(node)]['ub'] = bounds

    def setBounds(self, lb, ub, nodes=None):
        """
        Setter for the bounds of the variable.

        Args:
            lb: desired lower bounds of the variable
            ub: desired upper bounds of the variable
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        self.setLowerBounds(lb, nodes)
        self.setUpperBounds(ub, nodes)

    def setInitialGuess(self, val, nodes=None):
        """
        Setter for the initial guess of the variable.

        Args:
            val: desired initial guess of the variable
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        if nodes is None:
            nodes = self._nodes
        else:
            nodes = misc.checkNodes(nodes, self._nodes)

        val = misc.checkValueEntry(val)

        if val.shape[0] != self._dim:
            raise Exception('Wrong dimension of initial guess inserted.')

        for node in nodes:
            self._var_impl['n' + str(node)]['w0'] = val

    def getVarOffset(self, node):
        """
        Getter for the offset variable. An offset variable is used to point to the desired implemented instance of the abstract variable.

        Examples:
            Abstract variable "x". Horizon nodes are N.

            Implemented variable "x" --> x_0, x_1, ... x_N-1, x_N

            Offset variable "x-1" points FOR EACH NODE at variable "x" implemented at the PREVIOUS NODE.

        Args:
            val: desired initial guess of the variable
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        if node > 0:
            node = f'+{node}'

        if node in self.var_offset:
            return self.var_offset[node]
        else:

            createTag = lambda name, node: name + str(node) if node is not None else name

            new_tag = createTag(self._tag, node)
            var = OffsetVariable(self._tag, new_tag, self._dim, self._nodes, int(node), self._var_impl)

            self.var_offset[node] = var
        return var

    def getVarOffsetDict(self):
        """
        Getter for the offset variables.

        Returns:
            dict with all the offset variables referring to this abstract variable
        """
        return self.var_offset

    def _setNNodes(self, n_nodes):
        """
        set a desired number of nodes to the variable.

        Args:
            n_nodes: the desired number of nodes to be set
        """
        self._nodes = n_nodes
        self._project()

    def _project(self):
        """
        Implements the variable along the horizon nodes.
        Generates an ordered dictionary containing the implemented variables and its value at each node {node: {var, lb, ub, w0}}
        """
        new_var_impl = OrderedDict()

        for n in self._nodes:
            if 'n' + str(n) in self._var_impl:
                # when reprojecting, if the implemented variable is present already, use it. Do not create a new one.
                new_var_impl['n' + str(n)] = self._var_impl['n' + str(n)]
            else:
                var_impl = cs.SX.sym(self._tag + '_' + str(n), self._dim)
                new_var_impl['n' + str(n)] = dict()
                new_var_impl['n' + str(n)]['var'] = var_impl
                new_var_impl['n' + str(n)]['lb'] = np.full(self._dim, -np.inf)
                new_var_impl['n' + str(n)]['ub'] = np.full(self._dim, np.inf)
                new_var_impl['n' + str(n)]['w0'] = np.zeros(self._dim)

        # this is to keep the instance at the same memory position (since it is shared by the OffsetVariable)
        self._var_impl.clear()
        self._var_impl.update(new_var_impl)

    def getImpl(self, nodes=None):
        """
        Getter for the implemented variable.

        Args:
            node: node at which the variable is retrieved

        Returns:
            implemented instances of the abstract variable
        """
        # embed this in getVals? difference between cs.vertcat and np.hstack
        if nodes is None:
            nodes = self._nodes

        nodes = misc.checkNodes(nodes, self._nodes)

        var_impl = cs.vertcat(*[self._var_impl['n' + str(i)]['var'] for i in nodes])

        return var_impl

    def _getVals(self, val_type, nodes):
        """
        wrapper function to get the desired argument from the variable.

        Args:
            val_type: type of the argument to retrieve
            node: desired node at which the argument is retrieved. If not specified, this returns the desired argument at all nodes

        Returns:
            value/s of the desired argument
        """
        if nodes is None:
            nodes = self._nodes

        nodes = misc.checkNodes(nodes, self._nodes)

        vals = np.hstack([self._var_impl['n' + str(i)][val_type] for i in nodes])

        return vals

    def getLowerBounds(self, node=None):
        """
        Getter for the lower bounds of the variable.

        Args:
            node: desired node at which the lower bounds are retrieved. If not specified, this returns the lower bounds at all nodes

        Returns:
            value/s of the lower bounds

        """
        return self._getVals('lb', node)

    def getUpperBounds(self, node=None):
        """
        Getter for the upper bounds of the variable.

        Args:
            node: desired node at which the upper bounds are retrieved. If not specified, this returns the upper bounds at all nodes

        Returns:
            value/s of the upper bounds

        """
        return self._getVals('ub', node)

    def getBounds(self, node=None):
        """
        Getter for the bounds of the variable.

        Args:
            node: desired node at which the bounds are retrieved. If not specified, this returns the bounds at all nodes

        Returns:
            value/s of the bounds

        """
        return self.getLowerBounds(node), self.getUpperBounds(node)

    def getInitialGuess(self, node=None):
        """
        Getter for the initial guess of the variable.

        Args:
            node: desired node at which the initial guess is retrieved. If not specified, this returns the lower bounds at all nodes

        Returns:
            value/s of the bounds

        """
        return self._getVals('w0', node)

    def getImplDim(self):
        """
        Getter for the dimension of the implemented variables, considering all the nodes.

        Returns:
            dimension of the variable multiplied by number of nodes
        """
        return self.shape[0] * len(self.getNodes())

    def getNodes(self):
        """
        Getter for the active nodes of the variable.

        Returns:
            the nodes the variable is defined on
        """
        return self._nodes

    def getName(self):
        """
        getter for the name of the variable

        Returns:
            name of the variable
        """
        return self._tag

    def __reduce__(self):
        """
        Experimental function to serialize this element.

        Returns:
            instance of this element serialized
        """
        return (self.__class__, (self._tag, self._dim, self._nodes, ))

class InputVariable(Variable):
    """
    Input (Control) Variable of Horizon Problem.
    The variable is abstract, and gets implemented automatically over the horizon nodes except the last one.

    Examples:
        Abstract variable "x". Horizon nodes are N.

        Implemented variable "x" --> x_0, x_1, ... x_N-1
    """
    def __init__(self, tag, dim, nodes):
        """
        Initialize the Input Variable.

        Args:
            tag: name of the variable
            dim: dimension of the variable
            nodes: should always be N-1, where N is the number of horizon nodes
        """
        super(InputVariable, self).__init__(tag, dim, nodes)

class StateVariable(Variable):
    """
    State Variable of Horizon Problem.
    The variable is abstract, and gets implemented automatically over all the horizon nodes.

    Examples:
        Abstract variable "x". Horizon nodes are N.

        Implemented variable "x" --> x_0, x_1, ... x_N-1, x_N
    """
    def __init__(self, tag, dim, nodes):
        """
        Initialize the State Variable.

        Args:
            tag: name of the variable
            dim: dimension of the variable
            nodes: should always be N, where N is the number of horizon nodes
        """
        super(StateVariable, self).__init__(tag, dim, nodes)

class AbstractAggregate(ABC):
    """
    Abstract Aggregate of the Horizon Problem.
    Used to store more variables of the same nature.
    """
    def __init__(self, *args: AbstractVariable):
        """
        Initialize the Abstract Aggregate.

        Args:
            *args: abstract variables of the same nature
        """
        self.var_list : List[AbstractVariable] = [item for item in args]

    def getVars(self) -> cs.SX:
        """
        Getter for the variable stored in the aggregate.

        Returns:
            a SX vector of all the variables stored
        """
        return cs.vertcat(*self.var_list)

    def __iter__(self):
        """
        Aggregate can be treated as an iterable.
        """
        yield from self.var_list

    def __getitem__(self, ind):
        """
        Aggregate can be accessed with indexing.
        """
        return self.var_list[ind]

class OffsetAggregate(AbstractAggregate):
    """
        Offset Aggregate of the Horizon Problem.
        Used to store more offset variables of the same nature.
        """

    def __init__(self, *args):
        """
        Initialize the Aggregate.

        Args:
            *args: instances of abstract variables of the same nature
        """
        super().__init__(*args)

    def getVarIndex(self, name):
        """
        Return offset and dimension for the variable with given name,
        that must belong to this aggregate. The resulting pair is such
        that the following code returns the variable's SX value
            off, dim = self.getVarIndex(name='myvar')
            v = self.getVars()[off:off+dim]

        Args:
            name ([type]): [description]
        """
        names = [v.getName() for v in self.var_list]
        i = names.index(name)
        offset = sum(v.getDim() for v in self.var_list[:i])
        return offset, self.var_list[i].getDim()

class Aggregate(AbstractAggregate):
    """
    Aggregate of the Horizon Problem.
    Used to store more variables of the same nature.
    """
    def __init__(self, *args):
        """
        Initialize the Aggregate.

        Args:
            *args: instances of abstract variables of the same nature
        """
        super().__init__(*args)

    def getVarOffset(self, offset):
        """
        Getter for the offset variables contained in the aggregate.

        Returns:
            an abstract aggregate with all the offset variables referring to the relative abstract variables
        """
        var_list = list()
        for var in self.var_list:
            var_list.append(var.getVarOffset(offset))

        return OffsetAggregate(*var_list)

    def getVarIndex(self, name):
        """
        Return offset and dimension for the variable with given name, 
        that must belong to this aggregate. The resulting pair is such
        that the following code returns the variable's SX value
            off, dim = self.getVarIndex(name='myvar')
            v = self.getVars()[off:off+dim]

        Args:
            name ([type]): [description]
        """
        names = [v.getName() for v in self.var_list]
        i = names.index(name)
        offset = sum(v.getDim() for v in self.var_list[:i])
        return offset, self.var_list[i].getDim()

    def addVariable(self, var):
        """
        Adds a Variable to the Aggregate.

        Todo:
            Should check if variable type belongs to the aggregate type (no mixing!)

        Args:
            var: variable to be added to the aggregate
        """
        self.var_list.append(var)

    def setBounds(self, lb, ub, nodes=None):
        """
        Setter for the bounds of the variables in the aggregate.

        Args:
            lb: desired lower bounds of the variable
            ub: desired upper bounds of the variable
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        self.setLowerBounds(lb, nodes)
        self.setUpperBounds(ub, nodes)

    def setLowerBounds(self, lb, nodes=None):
        """
        Setter for the lower bounds of the variables in the aggregate.

        Args:
            bounds: list of desired bounds of the all the variables in the aggregate
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        idx = 0
        for var in self:
            nv = var.shape[0]
            var.setLowerBounds(lb[idx:idx+nv], nodes)
            idx += nv

    def setUpperBounds(self, ub, nodes=None):
        """
        Setter for the upper bounds of the variables in the aggregate.

        Args:
            bounds: list of desired bounds of the all the variables in the aggregate
            nodes: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes
        """
        idx = 0
        for var in self:
            nv = var.shape[0]
            var.setUpperBounds(ub[idx:idx+nv], nodes)
            idx += nv

    def getBounds(self, node):
        """
        Getter for the bounds of the variables in the aggregate.

        Args:
            node: which nodes the bounds are applied on. If not specified, the variable is bounded along ALL the nodes

        Returns:
            array of bound values of each variable in the aggregate

        todo:
            test this!
        """
        lb = self.getLowerBounds(node)
        ub = self.getUpperBounds(node)

        return lb, ub

    def getLowerBounds(self, node):
        """
        Getter for the lower bounds of the variables in the aggregate.

        Args:
            node: which nodes the lower bounds are applied on. If not specified, the variable is bounded along ALL the nodes

        Returns:
            array of lower bound values of each variable in the aggregate

        todo:
            test this!
        """
        return np.hstack([var.getLowerBounds(node) for var in self])

    def getUpperBounds(self, node):
        """
        Getter for the upper bounds of the variables in the aggregate.

        Args:
            node: which nodes the upper bounds are applied on. If not specified, the variable is bounded along ALL the nodes

        Returns:
            array of upper bound values of each variable in the aggregate

        todo:
            test this!
        """
        return np.hstack([var.getUpperBounds(node) for var in self])

class StateAggregate(Aggregate):
    """
    State Aggregate of the Horizon Problem.
    Used to store all the state variables.
    """
    def __init__(self, *args: StateVariable):
        """
        Initialize the State Aggregate.

        Args:
            *args: instances of state variables
        """
        super().__init__(*args)

class InputAggregate(Aggregate):
    """
    Input (Control) Aggregate of the Horizon Problem.
    Used to store all the control variables.
    """
    def __init__(self, *args: InputVariable):
        """
        Initialize the Input (Control) Aggregate.

        Args:
            *args: instances of input (control) variables
        """
        super().__init__(*args)

# todo what if this is completely useless? at the end of the day, I need this Container for:
#   .getVarAbstrDict() --> get all abstract variables (special attention for the past variables)
#   .getVarImpl(): ---> get implemented variable at node
#   .getVarImplList() ---> get all the implemented variables as list
#   .getVarImplDict() ---> get all the implemented variables as dict
#   Can I do something else? Right now .build() orders them like as follows:
#            (nNone: [vars..]
#                n0: [vars..],
#                n1: [vars..], ...)
#   but since order is everything I care about, is there a simpler way?
#   for var in self.vars:
#    all_impl += var.getAllImpl
#   this is ordered with the variables and I probably don't need build?
#            (x: [n0, n1, n2 ...]
#             y: [n0, n1, n2, ...]
#             z: [nNone, n0, n1, ...])

#
class VariablesContainer:
    """
    Container of all the variables of Horizon.
    It is used internally by the Problem to get the abstract and implemented variables.
    """
    def __init__(self, logger=None):
        """
        Initialize the Variable Container.

        Args:
           nodes: the number of nodes of the problem
           logger: a logger reference to log data
        """
        self._logger = logger

        self._vars = OrderedDict()
        self._pars = OrderedDict()

    def createVar(self, var_type, name, dim, active_nodes):
        """
        Create a variable and adds it to the Variable Container.

        Args:
            var_type: type of variable
            name: name of variable
            dim: dimension of variable
            active_nodes: nodes the variable is defined on
        """
        var = var_type(name, dim, active_nodes)
        self._vars[name] = var

        if self._logger:
            if self._logger.isEnabledFor(logging.DEBUG):
                self._logger.debug('Creating variable {} as {}'.format(name, var_type))

        return var

    def setVar(self, name, dim, active_nodes=None):
        """
        Creates a generic variable.

        Args:
            name: name of the variable
            dim: dimension of the variable
            active_nodes: nodes the variable is defined on. If not specified, a Single Variable is generated
        """
        if active_nodes is None:
            var_type = SingleVariable
        else:
            var_type = Variable

        var = self.createVar(var_type, name, dim, active_nodes)
        return var

    def setStateVar(self, name, dim, nodes):
        """
        Creates a State variable.

        Args:
            name: name of the variable
            dim: dimension of the variable
        """
        var = self.createVar(StateVariable, name, dim, nodes)
        return var

    def setInputVar(self, name, dim, nodes):
        """
        Creates a Input (Control) variable.

        Args:
            name: name of the variable
            dim: dimension of the variable
        """
        var = self.createVar(InputVariable, name, dim, nodes)
        return var

    def setSingleVar(self, name, dim):
        """
        Creates a Single variable.

        Args:
            name: name of the variable
            dim: dimension of the variable
        """
        var = self.createVar(SingleVariable, name, dim, None)
        return var

    def setParameter(self, name, dim, nodes):
        """
        Creates a Parameter.

        Args:
            name: name of the variable
            dim: dimension of the variable
            nodes: nodes the parameter is defined on. If not specified, all the horizon nodes are considered
        """
        par = Parameter(name, dim, nodes)
        self._pars[name] = par

        if self._logger:
            if self._logger.isEnabledFor(logging.DEBUG):
                self._logger.debug(f'Creating parameter "{name}"')

        return par

    def setSingleParameter(self, name, dim):
        """
        Creates a Single Variable.

        Args:
            name: name of the variable
            dim: dimension of the variable
        """
        par = SingleParameter(name, dim, None)
        self._pars[name] = par

        if self._logger:
            if self._logger.isEnabledFor(logging.DEBUG):
                self._logger.debug(f'Creating single parameter "{name}"')

        return par

    def getStateVars(self):
        """
        Getter for the state variables in the Variable Container.

        Returns:
            a dict with all the state variables
        """
        state_vars = dict()
        for name, var in self._vars.items():
            if isinstance(var, StateVariable):
                state_vars[name] = var

        return state_vars

    def getInputVars(self):
        """
        Getter for the input (control) variables in the Variable Container.

        Returns:
            a dict with all the input (control) variables
        """
        input_vars = dict()
        for name, var in self._vars.items():
            if isinstance(var, InputVariable):
                input_vars[name] = var

        return input_vars

    def getVarList(self, offset=True):
        """
        Getter for the abstract variables in the Variable Container. Used by the Horizon Problem.

        Args:
            offset: if True, get also the offset_variable

        Returns:
            a list with all the abstract variables
        """
        var_abstr_list = list()
        for name, var in self._vars.items():
            var_abstr_list.append(var)
            if offset:
                for var_offset in var.getVarOffsetDict().values():
                    var_abstr_list.append(var_offset)

        return var_abstr_list

    def getParList(self):
        """
        Getter for the abstract parameters in the Variable Container. Used by the Horizon Problem.

        Returns:
            a list with all the abstract parameters
        """
        par_abstr_list = list()
        for name, var in self._pars.items():
            par_abstr_list.append(var)

        return par_abstr_list

    def getVar(self, name=None):
        """
        Getter for the abstract variables in the Variable Container.

        Args:
            name: name of the variable to be retrieve

        Returns:
            a dict with all the abstract variables
        """
        if name is None:
            var_dict = self._vars
        else:
            var_dict = self._vars[name]
        return var_dict

    def getPar(self, name=None):
        """
        Getter for the abstract parameters in the Variable Container.

        Returns:
            a dict with all the abstract parameters
        """
        if name is None:
            par_dict = self._pars
        else:
            par_dict = self._pars[name]
        return par_dict

    def removeVar(self, var_name):
        if var_name in self._vars:
            del self._vars[var_name]
            return True
        else:
            return False

    def setNNodes(self, n_nodes):
        """
        set a desired number of nodes to the Variable Container.

        Args:
            n_nodes: the desired number of nodes to be set
        """
        self._nodes = n_nodes

        for var in self._vars.values():
            if isinstance(var, SingleVariable):
                pass
            elif isinstance(var, InputVariable):
                var._setNNodes(list(range(self._nodes-1))) # todo is this right?
            elif isinstance(var, StateVariable):
                var._setNNodes(list(range(self._nodes)))
            elif isinstance(var, Variable):
                var._setNNodes([node for node in var.getNodes() if node in list(range(self._nodes))])

        for par in self._pars.values():
            if isinstance(par, SingleParameter):
                pass
            elif isinstance(par, Parameter):
                par._setNNodes([node for node in par.getNodes() if node in list(range(self._nodes))])

    def serialize(self):
        """
        Serialize the Variable Container. Used to save it.

        Returns:
           instance of serialized Variable Container
        """
        raise Exception('serialize yet to be re-implemented')
        # todo how to do? I may use __reduce__ but I don't know how
        # for name, value in self.state_var.items():
        #     print('state_var', type(value))
        #     self.state_var[name] = value.serialize()

        # for name, value in self.state_var_prev.items():
        #     print('state_var_prev', type(value))
        #     self.state_var_prev[name] = value.serialize()

        for node, item in self._vars_impl.items():
            for name, elem in item.items():
                self._vars_impl[node][name]['var'] = elem['var'].serialize()

    def deserialize(self):
        """
        Deserialize the Variable Container. Used to load it.

        Returns:
           instance of deserialized Variable Container
        """
        raise Exception('deserialize yet to be re-implemented')
        # for name, value in self.state_var.items():
        #     self.state_var[name] = cs.SX.deserialize(value)
        #
        # for name, value in self.state_var_prev.items():
        #     self.state_var_prev[name] = cs.SX.deserialize(value)

        for node, item in self._vars_impl.items():
            for name, elem in item.items():
                self._vars_impl[node][name]['var'] = cs.SX.deserialize(elem['var'])

    # def __reduce__(self):
    #     return (self.__class__, (self.nodes, self.logger, ))

if __name__ == '__main__':

    # a = AbstractVariable('a', 2)
    # print(a.getName())
    p = StateVariable('x', 2, [0,2])
    exit()
    n_nodes = 10
    sv = VariablesContainer(n_nodes)
    x = sv.setStateVar('x', 2)

    exit()
    x = StateVariable('x', 2, 4)
    u = InputVariable('u', 2, 4)
    print(isinstance(u, StateVariable))


    exit()
    # x._project()
    # print('before serialization:', x)
    # print('bounds:', x.getBounds(2))
    # x.setBounds(2,2)
    # print('bounds:', x.getBounds(2))
    # print('===PICKLING===')
    # a = pickle.dumps(x)
    # print(a)
    # print('===DEPICKLING===')
    # x_new = pickle.loads(a)
    #
    # print(type(x_new))
    # print(x_new)
    # print(x_new.tag)
    #
    # print('bounds:', x.getBounds(2))
    # print(x.var_impl)
    # exit()

    # x = StateVariable('x', 2, 15)
    # print([id(val['var']) for val in x.var_impl.values()])
    # x._setNNodes(20)
    # print([id(val['var']) for val in x.var_impl.values()])

    n_nodes = 10
    sv = VariablesContainer(n_nodes)
    x = sv.setStateVar('x', 2)
    x_prev = x.createAbstrNode(-1)

    print(x_prev)
    print(type(x_prev))
    print(type(x))

    exit()
    sv.setStateVar('x', 2, -1)
    sv.setStateVar('y', 2)

    for k in range(n_nodes):
        sv.update(k)

    # print(sv.state_var)
    # print(sv.state_var_prev)
    pprint.pprint(sv.vars_impl)
    # pprint.pprint(sv.getVarAbstrDict())
    # pprint.pprint(sv.getVarImplDict())
    # pprint.pprint(sv.getVarImpl('x-1', 1))


    exit()
    # x_prev = sv.setVar('x', 2, -2)
    #
    # for i in range(4):
    #     sv.update(i)
    #
    # print('state_var_prev', sv.state_var_prev)
    # print('state_var_impl', sv.state_var_impl)
    #
    # print('sv.getVarAbstrDict()', sv.getVarAbstrDict())
    # print('sv.getVarAbstrList()', sv.getVarAbstrList())
    # print('sv.getVarImplList()', sv.getVarImplList())
    # print('sv.getVarImpl()', sv.getVarImpl('x-2', 0))

    print('===PICKLING===')
    sv_serialized = pickle.dumps(sv)
    print(sv_serialized)
    print('===DEPICKLING===')
    sv_new = pickle.loads(sv_serialized)

    print(sv_new.vars)
    print(sv_new.state_var_prev)
    print(sv_new.vars_impl)
    print(sv_new.getVarAbstrDict())
    print(sv_new.getVarImplDict())