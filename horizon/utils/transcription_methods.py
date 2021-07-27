import casadi as cs
import horizon.utils.integrators as integ

class TranscriptionsHandler:
    def __init__(self, prb, dt, state_dot=None, logger=None):

        self.logger = logger

        self.problem = prb
        self.integrator = None

        self.state_dot = state_dot
        self.dt = dt

        state_list = self.problem.getState().getList()
        state_prev_list = list()

        for var in state_list:
            state_prev_list.append(var.getVarOffset(-1))

        self.state = cs.vertcat(*state_list)
        self.state_prev = cs.vertcat(*state_prev_list)

        input_list = self.problem.getInput()
        input_prev_list = list()
        for var in input_list:
            input_prev_list.append(var.getVarOffset(-1))

        self.input = cs.vertcat(*input_list)
        self.input_prev = cs.vertcat(*input_prev_list)



    def setDefaultIntegrator(self):
        # todo cost should be optional

        opts = dict()
        opts['tf'] = self.dt

        dae = dict()
        dae['x'] = self.state
        dae['p'] = self.input
        dae['ode'] = self.state_dot
        dae['quad'] = cs.sumsqr(self.input)

        self.integrator = integ.RK4(dae=dae, opts=opts, casadi_type=cs.SX)

    def setIntegrator(self, integrator):
        self.integrator = integrator

    def __integrate(self, state, input):
        if self.integrator is None:
            if self.state_dot is None:
                raise Exception('Dynamics of the system is not specified. Missing "state_dot"')
            if self.logger:
                self.logger.warning('Integrator not set. Using default integrator RK4')
            self.setDefaultIntegrator()

        state_int = self.integrator(state, input)[0]
        return state_int

    def setMultipleShooting(self):

        state_int = self.__integrate(self.state_prev, self.input_prev)
        ms = self.problem.createConstraint('multiple_shooting', state_int - self.state, nodes=range(1, self.problem.getNNodes()))

