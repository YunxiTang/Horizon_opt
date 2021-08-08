#!/usr/bin/env python3

import horizon.problem as prb
import horizon.utils.plotter as plotter
import casadi as cs
import numpy as np
from horizon.utils.integrators import make_direct_collocation
import horizon.utils.transcription_methods as transmet
import matplotlib.pyplot as plt

n_nodes = 50
dt = 0.1
mu = 0.2
grav = 9.81
prob = prb.Problem(n_nodes)

p = prob.createStateVariable('pos', dim=2)
v = prob.createStateVariable('vel', dim=2)
F = prob.createInputVariable('force', dim=2)


state = prob.getState()
state_prev = state.getVarOffset(-1)
x = state.getVars()


xdot = cs.vertcat(v, F) #- mu*grav*np.sign(v)
prob.setDynamics(xdot)

th = transmet.TranscriptionsHandler(prob, dt)
th.setMultipleShooting()  # here I could use different integrators, such as RK2, Euler, ... (the default is RK4)
# th.setDirectCollocation(3)

# set initial state (rest in zero)
p.setBounds(lb=[0, 0], ub=[0, 0], nodes=0)
v.setBounds(lb=[0, 0], ub=[0, 0], nodes=0)

# final constraint
p.setBounds(lb=[1, 1], ub=[1, 1], nodes=n_nodes)
v.setBounds(lb=[0, 0], ub=[0, 0], nodes=n_nodes)


obs_center = np.array([0.5, 0.5])
obs_r = 0.4
obs = cs.sumsqr(p - obs_center) - obs_r

obs_cnsrt = prob.createIntermediateConstraint('obstacle', obs)
obs_cnsrt.setUpperBounds(np.inf)
# intermediate cost ( i want to minimize the force! )
prob.createIntermediateCost('cost', cs.sumsqr(F))

# solve
prob.createProblem()
solution = prob.solveProblem()

# plot
plot_all = True

if plot_all:
    hplt = plotter.PlotterHorizon(prob)
    hplt.plotVariables()
    hplt.plotFunctions()


plt.figure()
plt.plot(solution['pos'][0], solution['pos'][1])
plt.plot([0, 0], [0, 0], 'bo', markersize=12)
plt.plot([1, 1], [1, 1], 'g*', markersize=12)
circle = plt.Circle(obs_center, radius=obs_r, fc='r')
plt.gca().add_patch(circle)

plt.show()
