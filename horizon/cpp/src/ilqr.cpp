#include "ilqr.h"

using namespace horizon;
using namespace casadi_utils;

namespace cs = casadi;

cs::DM to_cs(const Eigen::MatrixXd& eig)
{
    cs::DM ret(cs::Sparsity::dense(eig.rows(), eig.cols()));
    std::copy(eig.data(), eig.data() + eig.size(), ret.ptr());
    return ret;
}

Eigen::MatrixXd to_eig(const cs::DM& cas)
{
    auto cas_dense = cs::DM::densify(cas, 0);
    return Eigen::MatrixXd::Map(cas_dense.ptr(), cas_dense.size1(), cas_dense.size2());
}

IterativeLQR::IterativeLQR(cs::Function fdyn,
                           int N):
    _nx(fdyn.size1_in(0)),
    _nu(fdyn.size1_in(1)),
    _N(N),
    _f(fdyn),
    _cost(N+1, IntermediateCost(_nx, _nu)),
    _constraint(N+1),
    _value(N+1, ValueFunction(_nx)),
    _dyn(N, Dynamics(_nx, _nu)),
    _bp_res(N, BackwardPassResult(_nx, _nu)),
    _constraint_to_go(_nx),
    _fp_res(_nx, _nu, _N),
    _tmp(_N)
{
    // set dynamics
    for(auto& d : _dyn)
    {
        d.setDynamics(_f);
    }

    // initialize trajectories
    _xtrj.setZero(_nx, _N+1);
    _utrj.setZero(_nu, _N);

    // a default cost so that it works out of the box
    set_default_cost();
}

void IterativeLQR::setIntermediateCost(const std::vector<casadi::Function> &inter_cost)
{
    if(inter_cost.size() != _N)
    {
        throw std::invalid_argument("wrong intermediate cost length");
    }

    for(int i = 0; i < _N; i++)
    {
        _cost[i].setCost(inter_cost[i]);
    }
}

void IterativeLQR::setFinalCost(const casadi::Function &final_cost)
{
    _cost.back().setCost(final_cost);
}

void IterativeLQR::setFinalConstraint(const casadi::Function &final_constraint)
{
    _constraint.back().setConstraint(final_constraint);
}

void IterativeLQR::setInitialState(const Eigen::VectorXd &x0)
{
    _xtrj.col(0) = x0;
}

const Eigen::MatrixXd &IterativeLQR::getStateTrajectory() const
{
    return _xtrj;
}

const Eigen::MatrixXd &IterativeLQR::getInputTrajectory() const
{
    return _utrj;
}

void IterativeLQR::linearize_quadratize()
{
    for(int i = 0; i < _N; i++)
    {
        auto xi = state(i);
        auto ui = input(i);
        auto xnext = state(i+1);
        _dyn[i].linearize(xi, ui);
        _dyn[i].computeDefect(xi, ui, xnext);
        _constraint[i].linearize(xi, ui);
        _cost[i].quadratize(xi, ui);
    }

    // handle final cost and constraint
    // note: these are only function of the state!
    _cost.back().quadratize(state(_N), input(_N-1)); // note: input not used here!
    _constraint.back().linearize(state(_N), input(_N-1)); // note: input not used here!
}

void IterativeLQR::backward_pass()
{
    // initialize backward recursion from final cost..
    _value.back().S = _cost.back().Q();
    _value.back().s = _cost.back().q();

    // ..and constraint
    _constraint_to_go.set(_constraint.back());

    // backward pass
    for(int i = _N-1; i >= 0; i--)
    {
        backward_pass_iter(i);
    }
}

void IterativeLQR::backward_pass_iter(int i)
{
    // constraint handling
    auto [nz, cdyn, ccost] = handle_constraints(i);
    const bool has_constraints = nz != _nu;

    // note: after handling constraints, we're actually optimizing an
    // auxiliary input z, where the original input u = lc + Lc*x + Lz*z

    // some shorthands

    // value function
    const auto& value_next = _value[i+1];
    const auto& Snext = value_next.S;
    const auto& snext = value_next.s;

    // intermediate cost
    const auto r = ccost.r;
    const auto q = ccost.q;
    const auto Q = ccost.Q;
    const auto R = ccost.R;
    const auto P = ccost.P;

    // dynamics
    const auto A = cdyn.A;
    const auto B = cdyn.B;
    const auto d = cdyn.d;

    // workspace
    auto& tmp = _tmp[i];

    // mapping to original input u
    const auto& lc = tmp.lc;
    const auto& Lc = tmp.Lc;
    const auto& Lz = tmp.Lz;

    // components of next node's value function (as a function of
    // current state and control via the dynamics)
    // note: first compute state-only components, since after constraints
    // there might be no input dof to optimize at all!
    tmp.s_plus_S_d.noalias() = snext + Snext*d;
    tmp.S_A.noalias() = Snext*A;

    tmp.hx.noalias() = q + A.transpose()*tmp.s_plus_S_d;
    tmp.Hxx.noalias() = Q + A.transpose()*tmp.S_A;


    // handle case where nz = 0, i.e. no nullspace left after constraints
    if(nz == 0)
    {
        // save solution
        auto& res = _bp_res[i];
        auto& L = res.Lfb;
        auto& l = res.du_ff;
        L = Lc;
        l = lc;

        // save optimal value function
        auto& value = _value[i];
        auto& S = value.S;
        auto& s = value.s;
        S = tmp.Hxx;
        s = tmp.hx;

        return;
    }

    // remaining components of next node's value function (if nz > 0)
    tmp.hu.noalias() = r + B.transpose()*tmp.s_plus_S_d;
    tmp.Huu.noalias() = R + B.transpose()*Snext*B;
    tmp.Hux.noalias() = P + B.transpose()*tmp.S_A;

    // set huHux = [hu Hux]
    tmp.huHux.resize(nz, 1+_nx);
    tmp.huHux.col(0) = tmp.hu;
    tmp.huHux.rightCols(_nx) = tmp.Hux;

    // todo: second-order terms from dynamics

    // solve linear system to get ff and fb terms
    // after solveInPlace we will have huHux = [-l, -L]
    tmp.llt.compute(tmp.Huu);
    tmp.llt.solveInPlace(tmp.huHux);

    // todo: check solution for nan, unable to solve, etc

    // save solution
    auto& res = _bp_res[i];
    auto& L = res.Lfb;
    auto& l = res.du_ff;
    L = -tmp.huHux.rightCols(_nx);
    l = -tmp.huHux.col(0);

    // map to original input u
    if(has_constraints)
    {
        l = lc + Lz*l;
        L = Lc + Lz*L;
    }

    // save optimal value function
    auto& value = _value[i];
    auto& S = value.S;
    auto& s = value.s;

    S.noalias() = tmp.Hxx - L.transpose()*tmp.Huu*L;
    s.noalias() = tmp.hx + tmp.Hux.transpose()*l + L.transpose()*(tmp.hu + tmp.Huu*l);

}

IterativeLQR::HandleConstraintsRetType IterativeLQR::handle_constraints(int i)
{
    // some shorthands for..

    // ..intermediate cost
    const auto& cost = _cost[i];
    const auto r = cost.r();
    const auto q = cost.q();
    const auto& Q = cost.Q();
    const auto& R = cost.R();
    const auto& P = cost.P();

    // ..dynamics
    auto& dyn = _dyn[i];
    const auto& A = dyn.A();
    const auto& B = dyn.B();
    const auto& d = dyn.d;

    // ..workspace
    auto& tmp = _tmp[i];
    auto& C = tmp.C;
    auto& D = tmp.D;
    auto& h = tmp.h;
    auto& svd = tmp.svd;
    auto& rotC = tmp.rotC;
    auto& roth = tmp.roth;
    auto& lc = tmp.lc;
    auto& Lc = tmp.Lc;
    auto& Lz = tmp.Lz;

    // no constraint to handle, do nothing
    if(_constraint_to_go.dim() == 0)
    {
        ConstrainedDynamics cd = {A, B, d};
        ConstrainedCost cc = {Q, R, P, q, r};
        return std::make_tuple(_nu, cd, cc);
    }

    // back-propagate constraint to go from next step to current step
    C = _constraint_to_go.C() * A;
    D = _constraint_to_go.C() * B;
    h = _constraint_to_go.h() - _constraint_to_go.C()*d;

    // number of constraints
    int nc = _constraint_to_go.dim();

    // svd of input matrix
    const double sv_ratio_thr = 1e-3;
    svd.compute(D, Eigen::ComputeFullU|Eigen::ComputeFullV);
    const auto& U = svd.matrixU();
    const auto& V = svd.matrixV();
    const auto& sv = svd.singularValues();
    svd.setThreshold(sv[0]*sv_ratio_thr);
    int rank = svd.rank();
    int ns_dim = _nu - rank;

    // rotate constraints
    rotC.noalias() = U.transpose()*C;
    roth.noalias() = U.transpose()*h;

    // compute component of control input due to constraints,
    // i.e. uc = Lc*x + +Lz*z + lc, where:
    //  *) lc = -V[:, 0:r]*sigma^-1*rot_h
    //  *) Lz = V[:, r:]
    //  *) Lc = -V[:, 0:r]*sigma^-1*rot_C
    lc.noalias() = -V.leftCols(rank) * roth.head(rank).cwiseQuotient(sv.head(rank));
    Lc.noalias() = -V.leftCols(rank) * sv.head(rank).cwiseInverse().asDiagonal() * rotC.topRows(rank);
    Lz.noalias() = V.rightCols(ns_dim);

    // remove satisfied constraints from constraint to go
    _constraint_to_go.set(rotC.bottomRows(nc - rank),
                          roth.tail(nc - rank));

    // modified cost and dynamics due to uc = uc(x, z)
    // note: our new control input will be z!
    tmp.Ac.noalias() = A + B*Lc;
    tmp.Bc.noalias() = B*Lz;
    tmp.dc.noalias() = d + B*lc;

    tmp.qc.noalias() = q + Lc.transpose()*(r + R*lc) + P.transpose()*Lc;
    tmp.rc.noalias() = Lz.transpose()*(r + R*lc);
    tmp.Qc.noalias() = Q + Lc.transpose()*R*Lc + Lc.transpose()*P + P.transpose()*Lc;
    tmp.Rc.noalias() = Lz.transpose()*R*Lz;
    tmp.Pc.noalias() = Lz.transpose()*(P + R*Lc);

    // return
    ConstrainedDynamics cd = {tmp.Ac, tmp.Bc, tmp.dc};
    ConstrainedCost cc = {tmp.Qc, tmp.Rc, tmp.Pc, tmp.qc, tmp.rc};
    return std::make_tuple(ns_dim, cd, cc);

}

bool IterativeLQR::forward_pass(double alpha)
{
    // start from current trajectory
    _fp_res.xtrj = _xtrj;
    _fp_res.utrj = _utrj;

    for(int i = 0; i < _N; i++)
    {
        forward_pass_iter(i, alpha);
    }

    // todo: add line search
    // for now, we always accept the step
    _xtrj = _fp_res.xtrj;
    _utrj = _fp_res.utrj;

    return true;
}

void IterativeLQR::forward_pass_iter(int i, double alpha)
{
    // note!
    // this function will update the control at t = i, and
    // the state at t = i+1

    // some shorthands
    const auto xnext = state(i+1);
    const auto xi = state(i);
    const auto ui = input(i);
    auto xi_upd = _fp_res.xtrj.col(i);
    auto& tmp = _tmp[i];
    tmp.dx = xi_upd - xi;

    // dynamics
    const auto& dyn = _dyn[i];
    const auto& A = dyn.A();
    const auto& B = dyn.B();
    const auto& d = dyn.d;

    // backward pass solution
    const auto& res = _bp_res[i];
    const auto& L = res.Lfb;
    auto l = alpha * res.du_ff;

    // update control
    auto ui_upd = ui + l + L*tmp.dx;
    _fp_res.utrj.col(i) = ui_upd;

    // update next state
    auto xnext_upd = xnext + (A + B*L)*tmp.dx + B*l + d;
    _fp_res.xtrj.col(i+1) = xnext_upd;
}

void IterativeLQR::set_default_cost()
{
    auto x = cs::SX::sym("x", _nx);
    auto u = cs::SX::sym("u", _nu);
    auto l = cs::Function("dfl_cost", {x, u},
                          {0.5*cs::SX::sumsqr(u)},
                          {"x", "u"}, {"l"});
    auto lf = cs::Function("dfl_cost_final", {x, u}, {0.5*cs::SX::sumsqr(x)},
                           {"x", "u"}, {"l"});
    setIntermediateCost(std::vector<cs::Function>(_N, l));
    setFinalCost(lf);
}

Eigen::Ref<Eigen::VectorXd> IterativeLQR::state(int i)
{
    return _xtrj.col(i);
}

Eigen::Ref<Eigen::VectorXd> IterativeLQR::input(int i)
{
    return _utrj.col(i);
}

const Eigen::MatrixXd &IterativeLQR::Dynamics::A() const
{
    return df.getOutput(0);
}

const Eigen::MatrixXd &IterativeLQR::Dynamics::B() const
{
    return df.getOutput(1);
}

IterativeLQR::Dynamics::Dynamics(int nx, int)
{
    d.setZero(nx);
}

Eigen::Ref<const Eigen::VectorXd> IterativeLQR::Dynamics::integrate(const Eigen::VectorXd &x, const Eigen::VectorXd &u)
{
    f.setInput(0, x);
    f.setInput(1, u);
    f.call();
    return f.getOutput(0);
}

void IterativeLQR::Dynamics::linearize(const Eigen::VectorXd &x, const Eigen::VectorXd &u)
{
    df.setInput(0, x);
    df.setInput(1, u);
    df.call();
}

void IterativeLQR::Dynamics::computeDefect(const Eigen::VectorXd& x,
                                           const Eigen::VectorXd& u,
                                           const Eigen::VectorXd& xnext)
{
    auto xint = integrate(x, u);
    d = xint - xnext;
}

void IterativeLQR::Dynamics::setDynamics(casadi::Function _f)
{
    f = _f;
    df = _f.factory("df", {"x", "u"}, {"jac:f:x", "jac:f:u"});
}

const Eigen::MatrixXd& IterativeLQR::IntermediateCost::Q() const
{
    return ddl.getOutput(0);
}

Eigen::Ref<const Eigen::VectorXd> IterativeLQR::IntermediateCost::q() const
{
    return dl.getOutput(0).col(0);
}

const Eigen::MatrixXd& IterativeLQR::IntermediateCost::R() const
{
    return ddl.getOutput(1);
}

Eigen::Ref<const Eigen::VectorXd> IterativeLQR::IntermediateCost::r() const
{
    return dl.getOutput(1).col(0);
}

const Eigen::MatrixXd& IterativeLQR::IntermediateCost::P() const
{
    return ddl.getOutput(2);
}

IterativeLQR::IntermediateCost::IntermediateCost(int, int)
{
}

void IterativeLQR::IntermediateCost::setCost(const casadi::Function &cost)
{
    l = cost;

    // note: use grad to obtain a column vector!
    dl = l.function().factory("dl", {"x", "u"}, {"grad:l:x", "grad:l:u"});
    ddl = dl.function().factory("ddl", {"x", "u"}, {"jac:grad_l_x:x", "jac:grad_l_u:u", "jac:grad_l_u:x"});

    // tbd: do something with this
    bool is_quadratic = ddl.function().jacobian().nnz_out() == 0;
    static_cast<void>(is_quadratic);
}

void IterativeLQR::IntermediateCost::quadratize(const Eigen::VectorXd &x, const Eigen::VectorXd &u)
{
    // compute cost gradient
    dl.setInput(0, x);
    dl.setInput(1, u);
    dl.call();

    // compute cost hessian
    ddl.setInput(0, x);
    ddl.setInput(1, u);
    ddl.call();

}

IterativeLQR::ValueFunction::ValueFunction(int nx)
{
    S.setZero(nx, nx);
    s.setZero(nx);
}

IterativeLQR::BackwardPassResult::BackwardPassResult(int nx, int nu)
{
    Lfb.setZero(nu, nx);
    du_ff.setZero(nu);
}

IterativeLQR::ForwardPassResult::ForwardPassResult(int nx, int nu, int N)
{
    xtrj.setZero(nx, N);
    utrj.setZero(nu, N);
}

IterativeLQR::ConstraintToGo::ConstraintToGo(int nx):
    _dim(0)
{
    const int c_max = nx*10;  // todo: better estimate
    _C.setZero(c_max, nx);
    _h.setZero(c_max);
}

void IterativeLQR::ConstraintToGo::set(Eigen::Ref<const Eigen::MatrixXd> C_to_add,
                                       Eigen::Ref<const Eigen::VectorXd> h_to_add)
{
    _dim = h_to_add.size();
    _C.topRows(_dim) = C_to_add;
    _h.head(_dim) = h_to_add;

    // todo: check we don't overflow the matrix!
}

void IterativeLQR::ConstraintToGo::set(const IterativeLQR::Constraint &constr)
{
    if(!constr.is_valid())
    {
        return;
    }

    set(constr.C(), constr.h());
}

void IterativeLQR::ConstraintToGo::clear()
{
    _dim = 0;
}

int IterativeLQR::ConstraintToGo::dim() const
{
    return _dim;
}

Eigen::Ref<const Eigen::MatrixXd> IterativeLQR::ConstraintToGo::C() const
{
    return _C.topRows(_dim);
}

Eigen::Ref<const Eigen::VectorXd> IterativeLQR::ConstraintToGo::h() const
{
    return _h.head(_dim);
}

const Eigen::MatrixXd &IterativeLQR::Constraint::C() const
{
    return df.getOutput(0);
}

const Eigen::MatrixXd &IterativeLQR::Constraint::D() const
{
    return df.getOutput(1);
}

Eigen::Ref<const Eigen::VectorXd> IterativeLQR::Constraint::h() const
{
    return f.getOutput(0).col(0);
}

bool IterativeLQR::Constraint::is_valid() const
{
    return f.is_valid();
}

IterativeLQR::Constraint::Constraint()
{
}

void IterativeLQR::Constraint::linearize(const Eigen::VectorXd& x, const Eigen::VectorXd& u)
{
    if(!is_valid())
    {
        return;
    }

    // compute constraint value
    f.setInput(0, x);
    f.setInput(1, u);
    f.call();

    // compute constraint jacobian
    df.setInput(0, x);
    df.setInput(1, u);
    df.call();
}

void IterativeLQR::Constraint::setConstraint(casadi::Function h)
{
    f = h;
    df = h.factory("dh", {"x", "u"}, {"jac:h:x", "jac:h:u"});
}
