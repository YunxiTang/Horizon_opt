#!/usr/bin/env python3

from casadi_kin_dyn import pycasadi_kin_dyn
from horizon.transcriptions.transcriptor import Transcriptor
import casadi as cs
import numpy as np
from horizon import problem
from horizon.utils import utils
from horizon.transcriptions import integrators
from horizon.solvers import solver
import matplotlib.pyplot as plt
import os


rviz_replay = True
plot_sol = True

path_to_examples = os.path.abspath(__file__ + "/../../../")
os.environ['ROS_PACKAGE_PATH'] += ':' + path_to_examples

# Create CasADi interface to Pinocchio
urdffile = os.path.join(path_to_examples, 'urdf', 'cart_pole.urdf')
urdf = open(urdffile, 'r').read()

# Create casadi interface to pinocchio
kindyn = pycasadi_kin_dyn.CasadiKinDyn(urdf)
nq = kindyn.nq()
nv = kindyn.nv()

# OPTIMIZATION PARAMETERS
ns = 100  # number of shooting nodes

# Create horizon problem
prb = problem.Problem(ns)

# Create problem STATE variables: x = [q, qdot]
q = prb.createStateVariable("q", nq)
qdot = prb.createStateVariable("qdot", nv)
x = prb.getState().getVars()

# Create problem CONTROL variables: tau = [u, 0], embed underactuation in control
u = prb.createInputVariable("u", 1)
tau = cs.vertcat(u, 0.)

# Create final time variable
tf = prb.createVariable("tf", 1)

# Create dynamics
fd = kindyn.aba()  # this is the forward dynamics function:
qddot = fd(q=q, v=qdot, tau=tau)['a'] # qddot = M^-1(tau - h)
x, xdot = utils.double_integrator(q, qdot, qddot) # xdot = [qdot, qddot]
prb.setDynamics(xdot)


L = 0.5*cs.dot(qdot, qdot)  # Objective term
dae = {'x': x, 'p': u, 'ode': xdot, 'quad': L}
F_integrator = integrators.RK4(dae, opts=None, casadi_type=cs.SX)

# Limits
q_min = [-0.5, -2.*np.pi]
q_max = [0.5, 2.*np.pi]
q_init = [0., 0.]

qdot_lims = np.array([100., 100.])
qdot_init = [0., 0.]

tau_lims = np.array([3000])
tau_init = [0]

tf_min = 1.
tf_max = 10.
tf_init = 3.

q.setBounds(q_min, q_max)
q.setBounds(q_init, q_init, nodes=0)
qdot.setBounds(-qdot_lims, qdot_lims)
qdot.setBounds(qdot_init, qdot_init, nodes=0)
u.setBounds(-tau_lims, tau_lims)
tf.setBounds(tf_min, tf_max)
tf.setInitialGuess(tf_init)

# Cost function
prb.createIntermediateCost("tau", cs.sumsqr(tau))
prb.createCost("min_tf", 1000.*cs.sumsqr(tf))

# Constraints
q_prev = q.getVarOffset(-1)
qdot_prev = qdot.getVarOffset(-1)
u_prev = u.getVarOffset(-1)
x_prev, _ = utils.double_integrator(q_prev, qdot_prev, fd(q=q_prev, v=qdot_prev, tau=cs.vertcat(u_prev, 0.))['a'])
dt = tf/ns

prb.setDt(dt)

x_int = F_integrator(x0=x_prev, p=u_prev, time=dt)
prb.createConstraint("multiple_shooting", x_int["xf"] - x, nodes=list(range(1, ns+1)), bounds=dict(lb=np.zeros(nv+nq), ub=np.zeros(nv+nq)))

prb.createFinalConstraint("up", q[1] - np.pi)
prb.createFinalConstraint("final_qdot", qdot)

# Creates problem
solver = solver.Solver.make_solver('ipopt', prb, opts=None)
solver.solve()

solution = solver.getSolutionDict()

q_hist = solution["q"]
tf_sol = solution["tf"]

print(f"Tf: {tf_sol.flatten()}")

if plot_sol:

    time = np.arange(0.0, tf_sol + 1e-6, tf_sol / ns)
    plt.figure()
    plt.plot(time, q_hist[0,:])
    plt.plot(time, q_hist[1,:])
    plt.suptitle('$\mathrm{Base \ Position}$', size = 20)
    plt.xlabel('$\mathrm{[sec]}$', size = 20)
    plt.ylabel('$\mathrm{[m]}$', size = 20)


if rviz_replay:

    from horizon.ros.replay_trajectory import replay_trajectory
    import roslaunch, rospy
    # set ROS stuff and launchfile

    uuid = roslaunch.rlutil.get_or_generate_uuid(None, False)
    roslaunch.configure_logging(uuid)
    launch = roslaunch.parent.ROSLaunchParent(uuid, [path_to_examples + "/replay/launch/cart_pole.launch"])
    launch.start()
    rospy.loginfo("'cart_pole_final_time' visualization started.")

    # visualize the robot in RVIZ
    joint_list=["cart_joint", "pole_joint"]
    replay_trajectory(tf_sol/ns, joint_list, solution['q']).replay(is_floating_base=False)

else:
    print("To visualize the robot trajectory, start the script with the '--replay' option.")




